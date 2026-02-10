import os
from typing import List, Optional

import numpy as np
import google.generativeai as genai
from dotenv import load_dotenv
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
import json

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

# Configure Gemini API
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env")

genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-3-pro-preview")

# Google Search API Configuration
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = os.getenv("GOOGLE_CUSTOM_SEARCH_ENGINE_ID")

if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
    print("Warning: GOOGLE_SEARCH_API_KEY or GOOGLE_SEARCH_ENGINE_ID not found in .env")
    print("Real-time search disabled. Will use AI-based market research only.")
    GOOGLE_SEARCH_AVAILABLE = False
else:
    GOOGLE_SEARCH_AVAILABLE = True

# Google Sheets API Configuration with OAuth2
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CLIENT_SECRET_FILE = "client_secret_623907757355-bt5gjq8eqivg5ebmv25avhjjotqkm7qi.apps.googleusercontent.com.json"
OAUTH_CREDENTIALS_FILE = "oauth_token.json"

def get_sheets_credentials():
    """Get OAuth2 credentials for Google Sheets API."""
    creds = None

    # Load existing token if available
    if os.path.exists(OAUTH_CREDENTIALS_FILE):
        creds = Credentials.from_authorized_user_file(OAUTH_CREDENTIALS_FILE, SCOPES)

    # If no valid credentials, create new ones using client secret file
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise ValueError(f"Client secret file not found: {CLIENT_SECRET_FILE}")
            
            # Create OAuth2 flow from client secret file
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE,
                SCOPES
            )
            creds = flow.run_local_server(port=0)
            
            # Save credentials for next use
            with open(OAUTH_CREDENTIALS_FILE, 'w') as token:
                token.write(creds.to_json())
    
    return creds


def search_google(query: str, num_results: int = 5) -> List[dict]:
    """
    Search using Google Custom Search API for real market data.
    Returns list of search results with title, snippet, and link.
    """
    if not GOOGLE_SEARCH_AVAILABLE:
        return "Google Search API not accessible. Cannot perform search."
    
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "key": GOOGLE_SEARCH_API_KEY,
            "cx": GOOGLE_SEARCH_ENGINE_ID,
            "num": num_results
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        results = response.json()
        search_results = []#Contains the search results in a structured format
        
        if "items" in results:
            for item in results["items"]:
                search_results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                    "source": "Google Search"
                })
        
        return search_results
    
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 403:
            print("⚠️  Google Search API quota exceeded (100 queries/day limit). Using AI-based research instead.({e.response.status_code})")
            print(e.response.text,e.response.headers)
        else:
            print(f"⚠️  Google Search API error ({e.response.status_code}). Using AI-based research instead.")
        return []
    except Exception as e:
        print(f"⚠️  Search API unavailable. Falling back to AI research: {type(e).__name__}")
        return []


def fetch_google_news_rss(query: str, max_items: int = 5) -> List[dict]:
    """Fetch results from Google News RSS for a query."""
    try:
        url = f"https://news.google.com/rss/search?q={quote(query)}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "GenesisAI/1.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall('.//item')[:max_items]:
            title = item.findtext('title', default='')
            link = item.findtext('link', default='')
            desc = item.findtext('description', default='')
            items.append({
                'title': title,
                'snippet': desc,
                'link': link,
                'source': 'Google News'
            })
        return items
    except Exception:
        return []


def fetch_hn_algolia(query: str, max_items: int = 5) -> List[dict]:
    """Fetch Hacker News results from the Algolia API (no auth needed)."""
    try:
        url = f"https://hn.algolia.com/api/v1/search?query={quote(query)}&hitsPerPage={max_items}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "GenesisAI/1.0"})
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for hit in data.get('hits', [])[:max_items]:
            title = hit.get('title') or hit.get('story_title') or ''
            link = hit.get('url') or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            snippet = hit.get('story_text') or hit.get('comment_text') or ''
            items.append({'title': title, 'snippet': snippet, 'link': link, 'source': 'Hacker News'})
        return items
    except Exception:
        return []


def fetch_reddit_rss(query: str, max_items: int = 5) -> List[dict]:
    """Fetch Reddit search results via RSS feed."""
    try:
        url = f"https://www.reddit.com/search.rss?q={quote(query)}&limit={max_items}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "GenesisAI/1.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall('.//item')[:max_items]:
            title = item.findtext('title', default='')
            link = item.findtext('link', default='')
            desc = item.findtext('description', default='')
            items.append({'title': title, 'snippet': desc, 'link': link, 'source': 'Reddit'})
        return items
    except Exception:
        return []


def query_real_market_data(query: str, k: int = 5) -> List[str]:
    """
    Fetch real-time market data using Google Search API.
    Falls back to AI-based market research if API is unavailable.
    """
    
    results = []
    
    # Try Google Search API first
    if GOOGLE_SEARCH_AVAILABLE:
        search_queries = [
            f"{query} startup competitors",
            f"{query} market size industry",
            f"{query} venture funding",
            f"companies in {query} space",
            f"{query} market trends 2026"
        ]
        
        all_results = []
        for search_query in search_queries:
            try:
                google_results = search_google(search_query, num_results=3)
                all_results.extend(google_results)
            except Exception as e:
                print(f"Error searching for '{search_query}': {str(e)}")
        
        # Format results
        if all_results:
            formatted_results = []
            for result in all_results[:k]:
                formatted_text = f"Title: {result['title']}\nSnippet: {result['snippet']}\nSource: {result['link']}"
                formatted_results.append(formatted_text)
            return formatted_results if formatted_results else ["No specific market data found from search."]
        else:
            # If Google Custom Search returned nothing, try alternative public sources
            print("📡 Google Custom Search unavailable; trying Google News RSS, Hacker News, and Reddit as fallback...")
            alt_results = []
            try:
                alt_results.extend(fetch_google_news_rss(query, max_items=3))
                alt_results.extend(fetch_hn_algolia(query, max_items=3))
                alt_results.extend(fetch_reddit_rss(query, max_items=3))
            except Exception as e:
                print(f"Error fetching alternative sources: {e}")

            if alt_results:
                formatted_results = []
                for result in alt_results[:k]:
                    formatted_text = f"Title: {result.get('title','')}\nSnippet: {result.get('snippet','')}\nSource: {result.get('link','')}"
                    formatted_results.append(formatted_text)
                return formatted_results if formatted_results else ["Market data unavailable from public sources."]
    
    # Fallback to AI-based research if neither Google Custom Search nor alternatives work
    fallback_prompt = f"""
You are a startup market research expert with access to current market knowledge.

Research and provide real market data about:
Query: {query}

Find and list:
1. Real existing startups or companies doing similar things
2. Current market trends and validated problems
3. Funding landscape and recent deals
4. Recent news or product launches in this space
5. Estimated market size and opportunity

For each relevant company/startup found, provide:
- Name
- What they do
- Current status/funding
- Key differentiation
- Market impact

Provide factual, specific information based on your knowledge.
"""

    try:
        response = model.generate_content(fallback_prompt)
        search_results = response.text
        
        # Parse results into list format
        result_list = []
        lines = search_results.split('\n')
        current_result = ""
        
        for line in lines:
            if line.strip():
                current_result += line + "\n"
                if len(current_result) > 400:
                    result_list.append(current_result.strip())
                    current_result = ""
                    
        if current_result:
            result_list.append(current_result.strip())
            
        return result_list[:k] if result_list else ["No market data available."]
        
    except Exception as e:
        print(f"Error in fallback market research: {str(e)}")
        return [f"Unable to fetch market data: {str(e)}"]


def get_validation_context(idea: str, k: int = 5) -> str:
    """
    Get real-time market validation data for an idea using Google Search.
    """
    results = query_real_market_data(idea, k)
    context = "\n\n".join(results)
    return context


def query(question: str, collection_name: str = "unicorns", k: int = 4, persist_directory: Optional[str] = None) -> List[str]:
    """
    Get real-time market context using Google Search API.
    Replaces the old local vector store approach.
    """
    return query_real_market_data(question, k)


#------------------ Financial Modeling Functions ------------------

def extract_financial_assumptions(idea: str) -> dict:
    """
    Uses real-time market context to extract
    structured financial assumptions for a 3-year model.
    """
    context = get_validation_context(idea)

    prompt = f"""
You are a venture capital financial analyst.

Based ONLY on the market evidence below, extract conservative,
realistic assumptions for a SaaS startup.

Market Evidence:
{context}

Return STRICT JSON only.
No explanations.
No markdown.

Schema:
{{
  "pricing_per_customer_per_year": number,
  "target_customers_year_1": number,
  "annual_growth_rate": number,
  "churn_rate": number,
  "confidence_level": "low" | "medium" | "high"
}}
"""

    response = model.generate_content(prompt)

    try:
        return json.loads(response.text)
    except Exception as e:
        raise ValueError(f"Invalid JSON from Gemini: {response.text}")
    




def calculate_3_year_revenue(assumptions: dict) -> dict:
    price = assumptions["pricing_per_customer_per_year"]
    users_y1 = assumptions["target_customers_year_1"]
    growth = assumptions["annual_growth_rate"]
    churn = assumptions["churn_rate"]

    users_y2 = int(users_y1 * (1 + growth) * (1 - churn))
    users_y3 = int(users_y2 * (1 + growth) * (1 - churn))

    return {
        "Year 1": {
            "Customers": users_y1,
            "Revenue": users_y1 * price
        },
        "Year 2": {
            "Customers": users_y2,
            "Revenue": users_y2 * price
        },
        "Year 3": {
            "Customers": users_y3,
            "Revenue": users_y3 * price
        }
    }




def write_revenue_to_sheets(spreadsheet_id: str, revenue_model: dict):
    creds = get_sheets_credentials()
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()

    values = [
        ["Metric", "Year 1", "Year 2", "Year 3"],
        [
            "Customers",
            revenue_model["Year 1"]["Customers"],
            revenue_model["Year 2"]["Customers"],
            revenue_model["Year 3"]["Customers"],
        ],
        [
            "Revenue ($)",
            revenue_model["Year 1"]["Revenue"],
            revenue_model["Year 2"]["Revenue"],
            revenue_model["Year 3"]["Revenue"],
        ],
    ]

    body = {
        "values": values
    }

    sheet.values().update(
        spreadsheetId=spreadsheet_id,
        range="A1:D4",
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()


def generate_revenue_model_to_sheets(idea: str, spreadsheet_id: str):
    assumptions = extract_financial_assumptions(idea)
    revenue_model = calculate_3_year_revenue(assumptions)
    write_revenue_to_sheets(spreadsheet_id, revenue_model)

    return {
        "assumptions": assumptions,
        "revenue_model": revenue_model
    }
