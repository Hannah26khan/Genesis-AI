from flask import Flask, request, render_template, jsonify, send_from_directory
from flask_cors import CORS
import google.generativeai as genai
import os
from dotenv import load_dotenv
import time
import rag
import base64
from werkzeug.utils import secure_filename
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# ------------------ SETUP ------------------
load_dotenv()

app = Flask(__name__)
CORS(app)

# File upload configuration
UPLOAD_FOLDER = "temp_uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf", "txt", "doc", "docx", "mp4", "webm", "avi", "mov", "mkv", "flv"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max

# Firebase Firestore initialization
firebase_cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
if firebase_cred_path and os.path.exists(firebase_cred_path):
    try:
        cred = credentials.Certificate(firebase_cred_path)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase Firestore initialized successfully")
    except Exception as e:
        print(f"⚠️  Firebase initialization failed: {e}")
        print("Continuing without Firestore...")
        db = None
else:
    print("⚠️  FIREBASE_CREDENTIALS_PATH not found in .env - Firestore disabled")
    db = None



# STARTING PAGE
@app.route("/")
def landing():
    return render_template("landing.html")

# GENESIS APP PAGE
@app.route("/app")
def app_page():
    return render_template("index.html")


api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env")

genai.configure(api_key=api_key)

# Initialize Gemini model (Google Search API calls handled by rag.py module)
model = genai.GenerativeModel(model_name="gemini-3-pro-preview")


def process_multimodal_files(files):
    """Uploads files to Gemini and returns the file objects."""
    gemini_files = []
    for file in files:
        if file.filename != "":
            path = os.path.join(UPLOAD_FOLDER, secure_filename(file.filename))
            file.save(path)
            # Upload to Gemini's file API
            g_file = genai.upload_file(path=path)
            # Wait for processing if it's a video
            while g_file.state.name == "PROCESSING":
                time.sleep(2)
                g_file = genai.get_file(g_file.name)
            gemini_files.append(g_file)
    return gemini_files




def generate_with_retry(prompt, max_retries=3, wait_time=10):
    """Generate content with retry logic for rate limits"""
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response
        except Exception as e:
            if "429" in str(e) or "Resource exhausted" in str(e):
                if attempt < max_retries - 1:
                    print(f"Rate limit hit, waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                else:
                    raise Exception(f"API quota exceeded after {max_retries} attempts. Please wait a few minutes and try again.")
            else:
                raise

# ------------------ IDEA GENERATOR ------------------
@app.route("/generate", methods=["POST"])
def generate():
    topic = request.form.get("topic")
    files = request.files.getlist("files")
    market_context = rag.get_validation_context(topic)
    # Process files for true multimodality
    processed_files = process_multimodal_files(files)
    
    if not topic:
        return jsonify({"error": "No topic provided"}), 400

    

    prompt = f"""
You are a world-class innovation strategist.

User topic and resources: {topic}{processed_files}
Use the real market data: {market_context}
Step 1: Identify 3 real-world mass problems in this domain.
Step 2: Pick the most impactful unsolved problem.
Step 3: Create a unique scalable startup solution. If possible add AI or tech to it.

Make sure:
- Useful for millions
- Practical but futuristic
- Scalable globally

Return strictly in format:

Problem:
Solution:
Target Users:
Why it's innovative:
"""

    try:
        response = generate_with_retry(prompt)
        idea_text = response.text

        # Save to Firestore
        if db:
            try:
                doc_ref = db.collection('ideas').document()
                doc_ref.set({
                    'type': 'generated',
                    'topic': topic,
                    'idea': idea_text,
                    'market_context': market_context,
                    'timestamp': firestore.SERVER_TIMESTAMP,
                    'has_files': len(files) > 0
                })
                print(f"✅ Idea saved to Firestore: {doc_ref.id}")
            except Exception as e:
                print(f"⚠️  Failed to save idea to Firestore: {e}")

        return jsonify({
            "status": "success",
            "idea": idea_text
        })

    except Exception as e:
        print(f"Error in generate: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to generate idea: {str(e)}"}), 500


# ------------------ VALIDATION ENGINE ------------------
@app.route("/validate", methods=["POST"])
def validate():
    data = request.get_json()
    idea = data.get("idea")

    if not idea:
        return jsonify({"error": "No idea provided"}), 400

    # Fetch real-time market data using Gemini's capabilities
    context_text = rag.get_validation_context(idea, k=5)

    validation_prompt = f"""
You are a startup market research and innovation analyst with access to real market data.

REAL MARKET DATA (from Google Search & current data):
{context_text}

Startup idea to validate:
{idea}

Based on REAL market data above:

Tasks:
1. Check if similar startups exist based on the real data.
2. Classify the idea:
   - Completely new/untapped market
   - Rare with few competitors
   - Common with multiple competitors

3. If rare → suggest specific uniqueness improvements based on market gaps.
4. If common → provide honest feedback and suggest better differentiation.

Return format:

Innovation Score: (0-10)
Market Saturation: Low/Medium/High
Verdict: ACCEPT / IMPROVE / REJECT
Real Competitors Found:
Market Gap Analysis:
Reason:
Recommendations:
"""

    try:
        response = generate_with_retry(validation_prompt)
        validation_text = response.text

        # Save to Firestore
        if db:
            try:
                doc_ref = db.collection('validations').document()
                doc_ref.set({
                    'idea': idea,
                    'validation': validation_text,
                    'market_context': context_text,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                print(f"✅ Validation saved to Firestore: {doc_ref.id}")
            except Exception as e:
                print(f"⚠️  Failed to save validation to Firestore: {e}")

        return jsonify({
            "status": "success",
            "validation": validation_text
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------ AUTO REGENERATE IF REJECTED ------------------
@app.route("/regenerate", methods=["POST"])
def regenerate():
    data = request.get_json()
    topic = data.get("topic")

    if not topic:
        return jsonify({"error": "No topic provided"}), 400

    regen_prompt = f"""
Create a highly disruptive startup idea about {topic}.

Rules:
- Must be new and not common
- Solve real mass problem
- Use AI or advanced tech
- Scalable globally
- Hackathon winning level

Return:

Problem:
Solution:
Target Users:
Why it's 10x better:
"""

    try:
        response = generate_with_retry(regen_prompt)

        return jsonify({
            "status": "success",
            "idea": response.text
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

#-----------------------------------------------Deep Validation Boardroom-----------------------------------------------
@app.route("/deepvalidate", methods=["POST"])
def deepvalidate():
    data = request.get_json()
    idea = data.get("idea")
    
    if not idea:
        return jsonify({"error": "No idea provided"}), 400

    # 1. Fetch Grounded Market Context
    print(f"[DeepValidate] Fetching market context for: {idea}")
    market_context = rag.get_validation_context(idea)
    print(f"[DeepValidate] Market context retrieved: {len(market_context)} chars")

    try:
        # --- STAGE 1: THE BRUTAL REALIST ---
        # Objective: Identify fatal flaws based on market reality.
        print("[DeepValidate] Stage 1: Generating Realist critique...")
        realist_prompt = f"""
        CONTEXT: {market_context}
        STARTUP IDEA: {idea}

        ROLE: You are a Brutal Realist VC Partner. 
        TASK: Identify the 3 most likely reasons this startup will FAIL within 12 months. 
        Focus on: Market saturation, technical debt, or unit economics. 
        Be specific, cynical, and data-driven.
        """
        realist_response = generate_with_retry(realist_prompt).text

        # --- STAGE 2: THE VISIONARY'S DEFENSE ---
        # Objective: Counter the critique and show adaptability.
        print("[DeepValidate] Stage 2: Generating Visionary defense...")
        visionary_prompt = f"""
        STARTUP IDEA: {idea}
        CRITIQUE FROM REALIST: 
        {realist_response}

        ROLE: You are the Visionary Founder. 
        TASK: Defend your concept. For every flaw the Realist mentioned, provide a specific 
        counter-strategy or pivot. Do not just be optimistic—be strategic. 
        How does your technology or business model bypass these 'kill-switches'?
        """
        visionary_response = generate_with_retry(visionary_prompt).text

        # --- STAGE 3: THE ANALYST'S VERDICT ---
        # Objective: Objective scoring of the interaction.
        print("[DeepValidate] Stage 3: Generating Analyst verdict...")
        analyst_prompt = f"""
        DEBATE LOG:
        Realist Critique: {realist_response}
        Visionary Defense: {visionary_response}

        ROLE: Senior Market Analyst.
        TASK: Synthesize the debate. 
        1. Did the Visionary effectively debunk the Realist's concerns?
        2. What is the 'Residual Risk' that still remains?
        3. FINAL VERDICT: INVEST / WATCH / PASS.
        4. CONFIDENCE SCORE: (0-100%) based on the validity of the Visionary's defense.
        """
        analyst_response = generate_with_retry(analyst_prompt).text

        # Aggregate the "Boardroom Minutes"
        full_analysis = f"""
        ## 🚪 Boardroom Debate: The Verdict
        
        ### 🛑 The Realist's Critique
        {realist_response}
        
        ---
        ### ⚡ The Visionary's Defense
        {visionary_response}
        
        ---
        ### 📊 Final Analyst Summary
        {analyst_response}
        """
        print(realist_response,visionary_response,analyst_response)

        print("[DeepValidate] Analysis complete, returning to client")
        
        # Save to Firestore
        if db:
            try:
                doc_ref = db.collection('deep_analyses').document()
                doc_ref.set({
                    'idea': idea,
                    'realist_critique': realist_response,
                    'visionary_defense': visionary_response,
                    'analyst_verdict': analyst_response,
                    'full_analysis': full_analysis,
                    'market_context': market_context,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                print(f"✅ Deep analysis saved to Firestore: {doc_ref.id}")
            except Exception as e:
                print(f"⚠️  Failed to save deep analysis to Firestore: {e}")
        
        return jsonify({
            "status": "success",
            "analysis": full_analysis
        
        })
        

    except Exception as e:
        print(f"[DeepValidate] ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "The boardroom collapsed. Please try again.", "details": str(e)}), 500

@app.route("/unicorn_predict", methods=["POST"])
def unicorn_predict():
    data = request.get_json()
    idea = data.get("idea")

    # Fetch real market data
    context_text = rag.get_validation_context(idea, k=5)

    prompt = f"""
You are a top venture capitalist with access to real market data and investment trends.

REAL MARKET DATA (from Google Search, Crunchbase & investment databases):
{context_text}

Startup idea to predict:
{idea}

Based on REAL market data, predict:

1. Unicorn probability (0-100%) - justify using real comparable companies
2. Timeline to unicorn status (if possible based on similar companies)
3. What must be done to reach billion dollar valuation (based on proven paths)
4. Biggest weakness compared to market leaders
5. Current investor interest level (based on funding trends in this space)
6. Key success metrics to track
7. Most critical next step

Return structured response with real data backing each point.
"""

    response = generate_with_retry(prompt)
    prediction_text = response.text

    # Save to Firestore
    if db:
        try:
            doc_ref = db.collection('predictions').document()
            doc_ref.set({
                'idea': idea,
                'prediction': prediction_text,
                'market_context': context_text,
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            print(f"✅ Prediction saved to Firestore: {doc_ref.id}")
        except Exception as e:
            print(f"⚠️  Failed to save prediction to Firestore: {e}")

    return jsonify({
        "status": "success",
        "prediction": prediction_text
    })



# ------------------ RAG ENDPOINTS (Google Search Powered) ------------------
@app.route("/ingest", methods=["POST"])
def ingest_endpoint():
    """
    Ingest endpoint - now uses Google Search API.
    No CSV ingestion needed with real-time market data.
    """
    try:
        return jsonify({
            "status": "success", 
            "message": "Google Search API is now active. Real-time market data is automatically fetched on each validation.",
            "method": "Google Custom Search API"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/rag/query", methods=["POST"])
def rag_query():
    data = request.get_json()
    question = data.get("question")

    if not question:
        return jsonify({"error": "No question provided"}), 400

    try:
        # Uses Google Search API via rag module
        contexts = rag.query(question, k=4)
        context_text = "\n\n".join(contexts)

        prompt = f"""
You are a helpful assistant with access to real market data via Google Search.

Context from real market search:
{context_text}

Question: {question}

Provide a helpful answer based on the real market data above."""

        response = generate_with_retry(prompt)
        
        return jsonify({
            "status": "success",
            "answer": response.text,
            "sources": contexts
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#------------------ FINANCIAL MODELING TO SHEETS ------------------

@app.route("/financials", methods=["POST"])
def generate_financials():
    data = request.get_json()
    idea = data.get("idea")
    spreadsheet_id = data.get("spreadsheet_id")

    if not idea or not spreadsheet_id:
        return jsonify({"error": "Missing idea or spreadsheet_id"}), 400

    try:
        result = rag.generate_revenue_model_to_sheets(
            idea=idea,
            spreadsheet_id=spreadsheet_id
        )

        # Optional: persist to Firestore
        if db:
            db.collection("financial_models").add({
                "idea": idea,
                "assumptions": result["assumptions"],
                "revenue_model": result["revenue_model"],
                "spreadsheet_id": spreadsheet_id,
                "timestamp": firestore.SERVER_TIMESTAMP
            })

        return jsonify({
            "status": "success",
            "assumptions": result["assumptions"],
            "revenue_model": result["revenue_model"],
            "spreadsheet_id": spreadsheet_id
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


#------------------ PROTOTYPE GENERATION ------------------

@app.route("/generate-prototype", methods=["POST"])
def generate_prototype():
    data = request.get_json()
    idea = data.get("idea")

    if not idea:
        return jsonify({"error": "No idea provided"}), 400

    try:
        prompt = f"""
You are a senior Flutter engineer.

Generate a demo-ready Flutter prototype UI for the following startup concept:

{idea}

Requirements:
- Use Flutter with Material 3
- Single-screen application
- AppBar with product name
- Hero section with short tagline
- 2–3 feature cards
- One primary call-to-action button
- Clean spacing, modern layout
- No backend logic
- No comments or explanations

Return ONLY valid Dart code for a Flutter app (main.dart).
Do not include markdown, backticks, or explanations.
"""

        response = generate_with_retry(prompt)
        flutter_code = response.text

        # Save to Firestore
        if db:
            try:
                doc_ref = db.collection("prototypes").document()
                doc_ref.set({
                    "idea": idea,
                    "flutter_code": flutter_code,
                    "timestamp": firestore.SERVER_TIMESTAMP
                })
                print(f"✅ Prototype saved to Firestore: {doc_ref.id}")
            except Exception as e:
                print(f"⚠️  Failed to save prototype to Firestore: {e}")

        return jsonify({
            "status": "success",
            "flutter_code": flutter_code
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ------------------ RUN SERVER ------------------
if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=5000)