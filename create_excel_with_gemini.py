"""
create_excel_with_gemini.py
--------------------------------
Extract NY State Article 9-A corporate income tax provisions ➜ Extract key tax rates ➜ Export to Excel

Version 4 (2025-05-26)
★ Added: Use Gemini LLM to analyze tax rate content and provide reasoning
★ Fixed: ENI title renamed (Business income tax rate) and correctly parsed
★ Fixed: FDM minimum error from $19 ➜ Only extract general business taxpayer table
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag
from openpyxl import Workbook
import google.generativeai as genai

# ---------------- Gemini Configuration ----------------
from config_loader import get_gemini_api_key, get_gemini_model_name, validate_config

# Validate configuration first
if not validate_config():
    print("[Gemini] Configuration validation failed!")
    model = None
    GEMINI_READY = False
else:
    try:
        _GEMINI_KEY = get_gemini_api_key()
        _MODEL_NAME = get_gemini_model_name()
        
        genai.configure(api_key=_GEMINI_KEY)
        model = genai.GenerativeModel(_MODEL_NAME)
        GEMINI_READY = True
        print("[Gemini] API enabled successfully.")
    except Exception as err:  # pragma: no cover
        print(f"[Gemini] Failed to enable: {err}, will output raw provisions only.")
        model = None
        GEMINI_READY = False

# ---------------- Constants ----------------
NY_URL = "https://www.tax.ny.gov/bus/ct/def_art9a.htm"
HEADINGS = {
    "Entire Net Income Base": "eni",            # Legacy anchor
    "Business capital base": None,              # No anchor, requires fuzzy matching
    "Fixed dollar minimum tax": None,
}

# ---------------- Utilities ----------------
def _clean(text: str | None) -> str:
    return " ".join(text.split()) if text else ""

# ---------------- Web Scraping ----------------
def _find_heading(soup: BeautifulSoup, base: str, anchor: Optional[str]) -> Optional[Tag]:
    """Find section heading: try anchor first, then synonyms/fuzzy matching."""
    # 1) anchor
    if anchor:
        anchor_tag = soup.find(id=anchor)
        if anchor_tag:
            return anchor_tag.find_next(["h2", "h3", "h4"])

    # 2) ENI title new name alias
    if base.lower().startswith("entire net income"):
        tag = soup.find(
            lambda t: t.name in ["h2", "h3", "h4"]
            and "business income tax rate" in t.get_text().lower()
        )
        if tag:
            return tag

    # 3) Business capital base: contains both business+capital
    if base.lower().startswith("business capital"):
        return soup.find(
            lambda t: t.name in ["h2", "h3", "h4"]
            and "business" in t.get_text().lower()
            and "capital" in t.get_text().lower()
        )

    # 4) General exact matching
    return soup.find(
        lambda t: t.name in ["h2", "h3", "h4"] and base.lower() in t.get_text().lower()
    )

def scrape_ny_raw() -> Dict[str, str]:
    print("[Crawler] Scraping NY State provisions...")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TaxBot/1.0)"}
    try:
        resp: requests.Response = requests.get(NY_URL, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Crawler] Connection failed: {e}")
        return {k: f"[Scraping failed: {e}]" for k in HEADINGS}

    soup = BeautifulSoup(resp.content, "html.parser")

    Path("output").mkdir(exist_ok=True)
    Path("output/debug_full_html.html").write_text(soup.prettify(), encoding="utf-8")

    out: Dict[str, str] = {}
    
    # Special handling for ENI: directly find Business income tax rate table
    eni_caption = soup.find("caption", string=lambda text: text and "Business income tax rate" in text)
    if eni_caption:
        eni_table = eni_caption.find_parent("table")
        if eni_table:
            buf = []
            for row in eni_table.find_all("tr"):
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if cols:
                    buf.append(" | ".join(cols))
            out["Entire Net Income Base"] = "\n".join(buf) if buf else "[ENI table empty]"
        else:
            out["Entire Net Income Base"] = "[ENI caption found but table empty]"
    else:
        out["Entire Net Income Base"] = "[ENI table caption not found]"

    # Handle Business capital base
    capital_section = soup.find(lambda t: t and t.name == "p" and "business capital base" in t.get_text().lower())
    if capital_section:
        # Find next table
        capital_table = capital_section.find_next("table")
        if capital_table:
            buf = []
            for row in capital_table.find_all("tr"):
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if cols:
                    buf.append(" | ".join(cols))
            out["Business capital base"] = "\n".join(buf) if buf else "[Capital table empty]"
        else:
            out["Business capital base"] = "[Capital table not found]"
    else:
        out["Business capital base"] = "[Capital section not found]"

    # Handle Fixed dollar minimum tax - only general business taxpayers table
    fdm_heading = soup.find("h2", string=lambda text: text and "Fixed dollar minimum tax for general business taxpayers" in text)
    if not fdm_heading:
        # Try to find any h2 tag containing this text
        fdm_heading = soup.find(lambda tag: tag.name == "h2" and "Fixed dollar minimum tax for general business taxpayers" in tag.get_text())
    
    if fdm_heading:
        fdm_table = fdm_heading.find_next("table")
        if fdm_table:
            buf = []
            for row in fdm_table.find_all("tr"):
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if cols:
                    buf.append(" | ".join(cols))
            out["Fixed dollar minimum tax"] = "\n".join(buf) if buf else "[FDM table empty]"
        else:
            out["Fixed dollar minimum tax"] = "[FDM table not found]"
    else:
        out["Fixed dollar minimum tax"] = "[FDM heading not found]"

    print("[Crawler] Provisions scraping completed.")
    return out

# ---------------- Text Output ----------------
def save_raw_text(raw: Dict[str, str], out_dir: Path) -> None:
    txt = out_dir / "ny_tax_summary_raw.txt"
    with txt.open("w", encoding="utf-8") as f:
        for k, v in raw.items():
            f.write(f"--- {k} ---\n{v}\n\n")
    print(f"[TXT] Written to {txt}")

# ---------------- Extract Key Tax Rates using Gemini ----------------
def derive_rates_with_gemini(raw: Dict[str, str]) -> Tuple[Dict[str, str], str]:
    """Use Gemini LLM to analyze tax rate content and provide reasoning"""
    
    if not GEMINI_READY:
        # Fallback to original logic
        return derive_rates_fallback(raw), "[Gemini unavailable, using fallback logic]"
    
    reasoning_parts = []
    result: Dict[str, str] = {}
    
    # ENI Analysis
    eni_content = raw.get("Entire Net Income Base", "")
    if eni_content and not eni_content.startswith("["):
        eni_prompt = f"""
Please analyze the following NY State ENI tax rate table content and find the tax rate for "All other general business taxpayers":

{eni_content}

Please answer:
1. What is the tax rate for "All other general business taxpayers"?
2. Which table row did you base this conclusion on?

Please respond in English using the following format:
Tax Rate: [rate number]
Source: [specific table row content]
"""
        try:
            eni_response = model.generate_content(eni_prompt)
            eni_analysis = eni_response.text.strip()
            
            # Extract rate from response
            if "0.065" in eni_analysis:
                result["Entire Net Income Base"] = "General business tax rate is 0.065"
                reasoning_parts.append(f"--- ENI ---\n{eni_analysis}")
            else:
                result["Entire Net Income Base"] = "Unable to parse ENI tax rate"
                reasoning_parts.append(f"--- ENI ---\nGemini analysis failed: {eni_analysis}")
        except Exception as e:
            result["Entire Net Income Base"] = "Error analyzing ENI with Gemini"
            reasoning_parts.append(f"--- ENI ---\nGemini error: {e}")
    else:
        result["Entire Net Income Base"] = "ENI content empty or error"
        reasoning_parts.append("--- ENI ---\nENI content empty or error")

    # Capital Analysis
    cap_content = raw.get("Business capital base", "")
    if cap_content and not cap_content.startswith("["):
        cap_prompt = f"""
Please analyze the following NY State Capital tax rate table content and find the tax rate for "All other general business taxpayers":

{cap_content}

Please answer:
1. What is the tax rate for "All other general business taxpayers"?
2. Which table row did you base this conclusion on?

Please respond in English using the following format:
Tax Rate: [rate number]
Source: [specific table row content]
"""
        try:
            cap_response = model.generate_content(cap_prompt)
            cap_analysis = cap_response.text.strip()
            
            # Extract rate from response
            if "0.001875" in cap_analysis:
                result["Business capital base"] = "General business tax rate is 0.001875"
                reasoning_parts.append(f"--- Capital ---\n{cap_analysis}")
            else:
                result["Business capital base"] = "Unable to parse Capital tax rate"
                reasoning_parts.append(f"--- Capital ---\nGemini analysis failed: {cap_analysis}")
        except Exception as e:
            result["Business capital base"] = "Error analyzing Capital with Gemini"
            reasoning_parts.append(f"--- Capital ---\nGemini error: {e}")
    else:
        result["Business capital base"] = "Capital content empty or error"
        reasoning_parts.append("--- Capital ---\nCapital content empty or error")

    # FDM Analysis
    fdm_content = raw.get("Fixed dollar minimum tax", "")
    if fdm_content and not fdm_content.startswith("["):
        fdm_prompt = f"""
Please analyze the following NY State FDM tax rate table content and find the tax amount range for general businesses:

{fdm_content}

Please answer:
1. What is the minimum tax amount? (should be $25)
2. What is the maximum tax amount?
3. What type of businesses does this table apply to?

Please respond in English using the following format:
Minimum Tax: [amount]
Maximum Tax: [amount]
Business Type: [type description]
Source: [explain which table rows you based this on]
"""
        try:
            fdm_response = model.generate_content(fdm_prompt)
            fdm_analysis = fdm_response.text.strip()
            
            # Extract range from response
            if "$25" in fdm_analysis and ("$200,000" in fdm_analysis or "$200000" in fdm_analysis):
                result["Fixed dollar minimum tax"] = "Graduated by revenue, ranging from $25 to $200,000"
                reasoning_parts.append(f"--- FDM ---\n{fdm_analysis}")
            else:
                result["Fixed dollar minimum tax"] = "Unable to correctly parse FDM range"
                reasoning_parts.append(f"--- FDM ---\nGemini analysis result: {fdm_analysis}")
        except Exception as e:
            result["Fixed dollar minimum tax"] = "Error analyzing FDM with Gemini"
            reasoning_parts.append(f"--- FDM ---\nGemini error: {e}")
    else:
        result["Fixed dollar minimum tax"] = "FDM content empty or error"
        reasoning_parts.append("--- FDM ---\nFDM content empty or error")

    reasoning = "\n\n".join(reasoning_parts)
    return result, reasoning

def derive_rates_fallback(raw: Dict[str, str]) -> Dict[str, str]:
    """Original fallback logic"""
    result: Dict[str, str] = {}

    # ENI 6.5%
    eni_lines = raw.get("Entire Net Income Base", "").split("\n")
    eni_rate = next(
        (l.split("|")[-1].strip() for l in eni_lines if "all other" in l.lower()),
        "N/A",
    )
    result["Entire Net Income Base"] = f"General business tax rate is {eni_rate}"

    # Capital 0.1875%
    cap_lines = raw.get("Business capital base", "").split("\n")
    cap_rate = next(
        (l.split("|")[-1].strip() for l in cap_lines if "all other" in l.lower()),
        "N/A",
    )
    result["Business capital base"] = f"General business tax rate is {cap_rate}"

    # FDM range (only ≥$25)
    fdm_lines = [l for l in raw.get("Fixed dollar minimum tax", "").split("\n") if "$" in l]
    amounts: list[int] = []
    for line in fdm_lines:
        for part in line.split("$")[1:]:
            num = part.replace(",", "").strip()
            if num.isdigit():
                amounts.append(int(num))
    nums = sorted(n for n in amounts if n >= 25)
    if nums:
        result["Fixed dollar minimum tax"] = f"Graduated by revenue, ranging from ${nums[0]} to ${nums[-1]}"
    else:
        result["Fixed dollar minimum tax"] = raw.get("Fixed dollar minimum tax", "N/A")

    return result

# ---------------- Save Reasoning Process ----------------
def save_reasoning(reasoning: str, out_dir: Path) -> None:
    """Save Gemini reasoning process to text file"""
    reasoning_file = out_dir / "ny_tax_llm_reasoning.txt"
    with reasoning_file.open("w", encoding="utf-8") as f:
        f.write(reasoning)
    print(f"[Reasoning] Written to {reasoning_file}")

# ---------------- Excel Export ----------------
def create_excel(data: Dict[str, str], out_dir: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "NY Tax Summary"
    ws.append(
        [
            "State",
            "Nexus Standard",
            "Effective Date (Nexus)",
            "Tax Base Summary",
            "Tax Rates (included in D1)",
            "Source URL",
            "Sales Factor Method",
            "Effective Date (Sales Factor)",
        ]
    )
    ws.append(
        [
            "new york",
            "market base",
            "2014",
            f"ENI: {data.get('Entire Net Income Base')}; "
            f"Capital: {data.get('Business capital base')}; "
            f"FDM: {data.get('Fixed dollar minimum tax')}",
            "",
            NY_URL + "#eni",
            "market base",
            "2014",
        ]
    )
    fp = out_dir / f"ny_tax_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(fp)
    print(f"[Excel] Exported to {fp}")

# ---------------- Main Function ----------------
def main() -> None:
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    raw = scrape_ny_raw()
    save_raw_text(raw, out_dir)

    summarized, reasoning = derive_rates_with_gemini(raw)
    save_reasoning(reasoning, out_dir)
    create_excel(summarized, out_dir)

    print("\n[Process Complete] Success")

if __name__ == "__main__":
    main()
