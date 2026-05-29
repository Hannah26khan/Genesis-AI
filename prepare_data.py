import os
try:
    import pandas as pd
except ImportError as e:
    raise ImportError("pandas is not installed. Run: python -m pip install pandas") from e

def load_documents(csv_path="data/unicorns.csv"):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"{csv_path} not found. Place your CSV at this path.")

    df = pd.read_csv(csv_path)
    documents = []

    for _, row in df.iterrows():
        company = row.get("Company", "")
        valuation = row.get("Valuation ($B)", "")
        country = row.get("Country", "")
        industry = row.get("Industry", "")
        investors = row.get("Investors", "")

        doc = f"""Company: {company}
Valuation: {valuation} Billion
Country: {country}
Industry: {industry}
Investors: {investors}

This company is a successful unicorn startup in {industry} sector.
"""
        documents.append(doc)

    return documents

if __name__ == "__main__":
    docs = load_documents()
    print("Total documents:", len(docs))
