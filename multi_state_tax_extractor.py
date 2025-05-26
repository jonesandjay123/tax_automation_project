"""
multi_state_tax_extractor.py
----------------------------
Scalable Multi-State Tax Rate Extraction Framework

This framework provides a configurable and extensible approach to extract
tax rates from any US state's tax websites using:
1. Configuration-driven approach (YAML/JSON configs per state)
2. LLM-powered intelligent content analysis
3. Fallback mechanisms for robustness
4. Unified output format

Author: Tax Automation Team
Version: 1.0 (2025-05-26)
"""

from __future__ import annotations

import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.hyperlink import Hyperlink
import google.generativeai as genai

# ---------------- Configuration Models ----------------

class TaxType(Enum):
    CORPORATE_INCOME = "corporate_income"
    FRANCHISE = "franchise"
    SALES_USE = "sales_use"
    PROPERTY = "property"

@dataclass
class StateConfig:
    """Configuration for each state's tax extraction"""
    state_name: str
    state_code: str  # e.g., "NY", "CA", "TX"
    
    # Website info
    base_url: str
    tax_definitions_url: str
    backup_urls: List[str] = None
    
    # Business context
    entity_type: str = "C_corp"  # C_corp, S_corp, LLC, etc.
    industry: str = "shipping"   # shipping, manufacturing, retail, etc.
    included_fields: List[str] = None  # ["ENI", "FDM", "Capital"]
    
    # Tax types to extract
    tax_types: List[TaxType] = None
    
    # Extraction hints for LLM
    extraction_hints: Dict[str, Any] = None
    
    # Fallback selectors (if LLM fails)
    fallback_selectors: Dict[str, str] = None
    
    # Output customization
    nexus_standard: str = "market base"
    nexus_effective_date: str = "unknown"
    sales_factor_method: str = "market base"
    sales_factor_date: str = "unknown"

# ---------------- LLM Analysis Engine ----------------

class TaxAnalysisEngine:
    """Unified LLM engine for analyzing tax content across states"""
    
    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        try:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
            self.available = True
            print(f"[LLM Engine] Successfully initialized with {model_name}")
        except Exception as e:
            print(f"[LLM Engine] Failed to initialize: {e}")
            self.model = None
            self.available = False
    
    def analyze_tax_content(self, content: str, state_name: str, config: StateConfig) -> Tuple[Dict[str, str], str]:
        """
        Analyze raw HTML/text content and extract tax rates using LLM
        
        Returns:
            - Dict: Extracted tax information
            - str: Reasoning process
        """
        if not self.available:
            return {}, "[LLM not available]"
        
        # Build industry-specific context
        industry_context = ""
        if config.industry == "shipping":
            industry_context = """
INDUSTRY CONTEXT: This analysis is for a SHIPPING/MARINE TRANSPORTATION company.
- Look for any special rates, exemptions, or rules for water transportation, marine services, or shipping companies
- Note any tonnage taxes, port fees, or maritime-specific tax structures
- Identify if standard corporate rates apply or if there are industry-specific overrides
"""
        
        # Build entity-specific context
        entity_context = ""
        if config.entity_type == "C_corp":
            entity_context = """
ENTITY TYPE: This is for a C-CORPORATION (regular corporation).
- Focus ONLY on rates applicable to C-corporations
- IGNORE any rules for S-corporations, LLCs, partnerships, sole proprietorships
- IGNORE special rules for banks, insurance companies, utilities, or REITs
- Look for rates applicable to "general business taxpayers" or "all other corporations"
"""

        # Determine which fields to extract
        included_fields = config.included_fields or ["ENI", "FDM", "Capital"]
        fields_instruction = f"Focus on extracting these specific tax components: {', '.join(included_fields)}"

        # Check if this is NY (use detailed descriptions) or other states (use comprehensive JSON)
        if state_name.lower() == "new york":
            # NY specific prompt for detailed descriptions
            prompt = f"""
You are a tax analysis expert specializing in {config.entity_type.replace('_', '-')} taxation in the {config.industry} industry.

{entity_context}
{industry_context}

CONTENT TO ANALYZE:
{content[:8000]}  # Limit content size

EXTRACTION REQUIREMENTS:
{fields_instruction}

1. ENI (Entire Net Income): Standard corporate income tax rate
2. FDM (Fixed Dollar Minimum): Minimum tax amounts or ranges
3. Capital: Capital-based tax rates (if applicable)

OUTPUT FORMAT:
Please respond in JSON format with this structure:
{{
    "ENI_description": "Complete sentence describing ENI tax rates with full context and conditions, or N/A",
    "FDM_description": "Complete sentence describing FDM tax amounts with ranges and conditions, or N/A", 
    "Capital_description": "Complete sentence describing Capital tax rates with limits and conditions, or N/A",
    "shipping_special_rule": "Any special rule for shipping industry or N/A",
    "reasoning": "Brief technical analysis summary",
    "confidence": "high/medium/low",
    "source_sections": ["list of HTML sections or table names used"]
}}

DESCRIPTION REQUIREMENTS:
- ENI_description: Include the exact tax rate(s), thresholds, and conditions
- FDM_description: Include the range and basis for calculation
- Capital_description: Include the rate and any limits
- Each description should be a complete, client-friendly sentence that can stand alone

CRITICAL: Only include rates that apply to {config.entity_type.replace('_', '-')}s in {config.industry}.
If no specific information is found, mark as "N/A" and explain why in reasoning.
"""
        else:
            # Other states: comprehensive JSON for user review
            prompt = f"""
You are a tax analysis expert specializing in {config.entity_type.replace('_', '-')} taxation in the {config.industry} industry.

{entity_context}
{industry_context}

CONTENT TO ANALYZE:
{content[:8000]}  # Limit content size

Please extract ALL available tax information for {state_name} state that applies to {config.entity_type.replace('_', '-')}s in {config.industry}.

OUTPUT FORMAT:
Please respond in JSON format with this structure:
{{
    "corporate_income_tax": "Rate and description or N/A",
    "franchise_tax": "Rate and description or N/A",
    "minimum_tax": "Amount/range and description or N/A",
    "capital_tax": "Rate and description or N/A",
    "gross_receipts_tax": "Rate and description or N/A",
    "alternative_minimum_tax": "Rate and description or N/A",
    "surcharge_tax": "Rate and description or N/A",
    "special_industry_rates": "Any shipping/transportation specific rates or N/A",
    "exemptions": "Any available exemptions or N/A",
    "thresholds": "Income/revenue thresholds that affect rates or N/A",
    "other_taxes": "Any other relevant business taxes or N/A",
    "reasoning": "Summary of analysis and what tax structures apply",
    "confidence": "high/medium/low",
    "source_sections": ["list of HTML sections or table names used"]
}}

CRITICAL: Include ALL relevant tax information found, even if it doesn't fit standard categories.
Mark items as "N/A" only if truly not found or not applicable.
"""
        
        try:
            response = self.model.generate_content(prompt)
            analysis_text = response.text.strip()
            
            # Clean up JSON response - remove markdown formatting if present
            if analysis_text.startswith("```json"):
                analysis_text = analysis_text.replace("```json", "").replace("```", "").strip()
            elif analysis_text.startswith("```"):
                analysis_text = analysis_text.replace("```", "").strip()
            
            # Try to parse JSON response
            try:
                analysis_data = json.loads(analysis_text)
                reasoning = analysis_data.get("reasoning", "No reasoning provided")
                confidence = analysis_data.get("confidence", "unknown")
                
                if state_name.lower() == "new york":
                    # NY specific processing - detailed descriptions
                    eni_desc = analysis_data.get("ENI_description", "N/A")
                    fdm_desc = analysis_data.get("FDM_description", "N/A") 
                    capital_desc = analysis_data.get("Capital_description", "N/A")
                    shipping_rule = analysis_data.get("shipping_special_rule", "N/A")
                    
                    # Build result based on included fields - using full descriptions
                    included_fields = config.included_fields or ["ENI", "FDM", "Capital"]
                    result = {}
                    
                    if "ENI" in included_fields and eni_desc != "N/A":
                        result["ENI (Entire Net Income)"] = eni_desc
                    if "FDM" in included_fields and fdm_desc != "N/A":
                        result["FDM (Fixed Dollar Minimum)"] = fdm_desc
                    if "Capital" in included_fields and capital_desc != "N/A":
                        result["Capital (Business Capital Base)"] = capital_desc
                    
                    # Format enhanced reasoning log
                    reasoning_log = f"""--- {state_name} Analysis ---
ENI: {eni_desc}
FDM: {fdm_desc}
Capital: {capital_desc}
Special shipping rule: {shipping_rule}
Reasoning: {reasoning}
Confidence: {confidence}"""
                    
                else:
                    # Other states - comprehensive JSON data for user review
                    result = {}
                    json_data_parts = []
                    
                    # Include all non-N/A tax information
                    for key, value in analysis_data.items():
                        if key not in ["reasoning", "confidence", "source_sections"] and value != "N/A":
                            result[key] = value
                            json_data_parts.append(f"{key}: {value}")
                    
                    # Format reasoning log with all data
                    reasoning_log = f"""--- {state_name} Analysis ---
{chr(10).join(json_data_parts)}
Reasoning: {reasoning}
Confidence: {confidence}"""
                
                return result, reasoning_log
                
            except json.JSONDecodeError:
                # Fallback: treat as plain text
                return {"Raw Analysis": analysis_text}, f"--- {state_name} Analysis ---\n{analysis_text}"
                
        except Exception as e:
            return {}, f"--- {state_name} Analysis ---\nLLM Error: {e}"

# ---------------- Web Scraping Engine ----------------

class StateWebScraper:
    """Intelligent web scraper that adapts to different state websites"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; TaxBot/2.0; +https://taxautomation.com/bot)'
        })
    
    def scrape_state_content(self, config: StateConfig) -> str:
        """
        Scrape content from state website with fallback mechanisms
        """
        urls_to_try = [config.tax_definitions_url]
        if config.backup_urls:
            urls_to_try.extend(config.backup_urls)
        
        for url in urls_to_try:
            try:
                print(f"[Scraper] Attempting to scrape {config.state_name} from {url}")
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, "html.parser")
                
                # Remove unwanted elements
                for element in soup(["script", "style", "nav", "footer", "header"]):
                    element.decompose()
                
                # Try to find main content area
                main_content = self._extract_main_content(soup, config)
                
                if main_content:
                    print(f"[Scraper] Successfully scraped {config.state_name}")
                    return str(main_content)
                else:
                    print(f"[Scraper] Warning: No main content found for {config.state_name}")
                    return str(soup)
                    
            except Exception as e:
                print(f"[Scraper] Failed to scrape {url}: {e}")
                continue
        
        return f"[Error] Failed to scrape any URLs for {config.state_name}"
    
    def _extract_main_content(self, soup: BeautifulSoup, config: StateConfig) -> Optional[BeautifulSoup]:
        """
        Intelligently extract the main tax content from the page
        """
        # Try common content selectors
        content_selectors = [
            "main",
            "[role='main']",
            ".main-content",
            ".content",
            "#content",
            ".tax-content"
        ]
        
        # Add state-specific selectors if provided
        if config.fallback_selectors:
            content_selectors.extend(config.fallback_selectors.get("content_area", []))
        
        for selector in content_selectors:
            content = soup.select_one(selector)
            if content:
                return content
        
        # Fallback: return body content
        return soup.find("body")

# ---------------- Multi-State Controller ----------------

class MultiStateTaxExtractor:
    """Main controller for multi-state tax extraction"""
    
    def __init__(self, api_key: str):
        self.llm_engine = TaxAnalysisEngine(api_key)
        self.scraper = StateWebScraper()
        self.results = {}
        self.reasoning_log = {}
    
    def load_state_config(self, config_path: Path) -> StateConfig:
        """Load state configuration from YAML or JSON file"""
        with open(config_path, 'r') as f:
            if config_path.suffix.lower() == '.yaml' or config_path.suffix.lower() == '.yml':
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
        
        return StateConfig(**data)
    
    def extract_state_taxes(self, config: StateConfig) -> Dict[str, str]:
        """Extract tax information for a single state"""
        print(f"\n[Extractor] Processing {config.state_name}...")
        
        # 1. Scrape website content
        raw_content = self.scraper.scrape_state_content(config)
        
        # 2. Analyze with LLM
        analysis_result, reasoning = self.llm_engine.analyze_tax_content(
            raw_content, 
            config.state_name, 
            config
        )
        
        # 3. Store results
        self.results[config.state_code] = {
            "config": config,
            "analysis": analysis_result,
            "raw_content": raw_content[:1000] + "..." if len(raw_content) > 1000 else raw_content
        }
        self.reasoning_log[config.state_code] = reasoning
        
        return analysis_result
    
    def process_multiple_states(self, config_dir: Path) -> Dict[str, Dict[str, str]]:
        """Process multiple states from a configuration directory"""
        results = {}
        
        # Find all config files
        config_files = list(config_dir.glob("*.yaml")) + list(config_dir.glob("*.yml")) + list(config_dir.glob("*.json"))
        
        for config_file in config_files:
            try:
                state_config = self.load_state_config(config_file)
                state_results = self.extract_state_taxes(state_config)
                results[state_config.state_code] = state_results
            except Exception as e:
                print(f"[Extractor] Failed to process {config_file}: {e}")
                results[config_file.stem] = {"error": str(e)}
        
        return results
    
    def export_results(self, output_dir: Path):
        """Export results to Excel and text files"""
        output_dir.mkdir(exist_ok=True)
        
        # 1. Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Multi-State Tax Summary"
        
        # Headers
        headers = [
            "State", "State Code", "Nexus Standard", "Effective Date (Nexus)",
            "Tax Base Summary", "Tax Rates", "Source URL",
            "Sales Factor Method", "Effective Date (Sales Factor)"
        ]
        ws.append(headers)
        
        # Data rows
        row_num = 2  # Start from row 2 (after headers)
        for state_code, result_data in self.results.items():
            config = result_data["config"]
            analysis = result_data["analysis"]
            
            # Format tax summary based on included fields and non-N/A values
            summary_parts = []
            
            for key, value in analysis.items():
                if value and value != "N/A":
                    # Use the full description as-is, with proper formatting
                    summary_parts.append(f"**{key}:** {value}")
            
            tax_summary = "\n\n".join(summary_parts) if summary_parts else "No applicable rates found"
            
            # Add entity type and industry context
            context_info = f"\n\n({config.entity_type.replace('_', '-')} in {config.industry})"
            
            row = [
                config.state_name,
                config.state_code,
                config.nexus_standard,
                config.nexus_effective_date,
                f"{tax_summary} {context_info}",
                "",  # Tax rates (included in summary)
                config.tax_definitions_url,
                config.sales_factor_method,
                config.sales_factor_date
            ]
            ws.append(row)
            
            # Add hyperlink to Source URL (column G, index 7)
            url_cell = ws.cell(row=row_num, column=7)
            url_cell.hyperlink = config.tax_definitions_url
            url_cell.font = Font(color="0000FF", underline="single")  # Blue and underlined
            
            row_num += 1
        
        # Save Excel
        excel_path = output_dir / f"multi_state_tax_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        wb.save(excel_path)
        print(f"[Export] Excel saved to {excel_path}")
        
        # 2. Save reasoning log
        reasoning_path = output_dir / "multi_state_reasoning_log.txt"
        with open(reasoning_path, 'w') as f:
            for state_code, reasoning in self.reasoning_log.items():
                f.write(f"=== {state_code} ===\n{reasoning}\n\n")
        print(f"[Export] Reasoning log saved to {reasoning_path}")

# ---------------- Example Usage ----------------

def create_example_state_configs():
    """Create example configuration files for different states"""
    
    configs_dir = Path("state_configs")
    configs_dir.mkdir(exist_ok=True)
    
    # New York config
    ny_config = {
        "state_name": "New York",
        "state_code": "NY",
        "base_url": "https://www.tax.ny.gov",
        "tax_definitions_url": "https://www.tax.ny.gov/bus/ct/def_art9a.htm",
        "backup_urls": [
            "https://www.tax.ny.gov/bus/ct/ctidx.htm"
        ],
        "tax_types": ["corporate_income"],
        "extraction_hints": {
            "eni_keywords": ["entire net income", "business income tax rate"],
            "capital_keywords": ["business capital base"],
            "fdm_keywords": ["fixed dollar minimum tax"]
        },
        "nexus_standard": "market base",
        "nexus_effective_date": "2014",
        "sales_factor_method": "market base",
        "sales_factor_date": "2014"
    }
    
    # California config (example)
    ca_config = {
        "state_name": "California",
        "state_code": "CA",
        "base_url": "https://www.ftb.ca.gov",
        "tax_definitions_url": "https://www.ftb.ca.gov/businesses/index.html",
        "backup_urls": [
            "https://www.ftb.ca.gov/forms/search/?query=corporation"
        ],
        "tax_types": ["corporate_income"],
        "extraction_hints": {
            "keywords": ["corporation tax", "franchise tax", "tax rate"]
        },
        "nexus_standard": "market base",
        "nexus_effective_date": "2018",
        "sales_factor_method": "market base", 
        "sales_factor_date": "2018"
    }
    
    # Save configs
    with open(configs_dir / "ny.yaml", 'w') as f:
        yaml.dump(ny_config, f, default_flow_style=False)
    
    with open(configs_dir / "ca.yaml", 'w') as f:
        yaml.dump(ca_config, f, default_flow_style=False)
    
    print(f"[Setup] Example state configs created in {configs_dir}")

def main():
    """Multi-state tax extraction for C-Corp shipping companies"""
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Extract tax rates for C-Corporation shipping companies')
    parser.add_argument('--entity_type', default='C_corp', 
                       help='Entity type (C_corp, S_corp, LLC, etc.)')
    parser.add_argument('--industry', default='shipping',
                       help='Industry type (shipping, manufacturing, retail, etc.)')
    parser.add_argument('--states', nargs='*', default=['NY', 'CA', 'TX', 'FL', 'IL'],
                       help='States to process (default: NY CA TX FL IL)')
    
    args = parser.parse_args()
    
    # Setup - Load API key securely
    from config_loader import get_gemini_api_key, validate_config
    
    if not validate_config():
        print("[Error] Configuration validation failed!")
        return
    
    print(f"[Config] Processing {len(args.states)} states for {args.entity_type.replace('_', '-')} in {args.industry} industry")
    print(f"[Config] States: {', '.join(args.states)}")
    
    api_key = get_gemini_api_key()
    output_dir = Path("multi_state_output")
    
    # Initialize extractor
    extractor = MultiStateTaxExtractor(api_key)
    
    # Process specified states only
    results = {}
    configs_dir = Path("state_configs")
    
    for state_code in args.states:
        config_file = configs_dir / f"{state_code.lower()}.yaml"
        if config_file.exists():
            try:
                state_config = extractor.load_state_config(config_file)
                
                # Override entity type and industry if specified
                if args.entity_type != 'C_corp' or args.industry != 'shipping':
                    state_config.entity_type = args.entity_type
                    state_config.industry = args.industry
                    print(f"[Override] {state_code}: Using {args.entity_type} + {args.industry}")
                
                state_results = extractor.extract_state_taxes(state_config)
                results[state_code] = state_results
            except Exception as e:
                print(f"[Error] Failed to process {state_code}: {e}")
                results[state_code] = {"error": str(e)}
        else:
            print(f"[Warning] Config file not found for {state_code}: {config_file}")
    
    # Export results
    extractor.export_results(output_dir)
    
    print(f"\n[Complete] Processed {len(results)} states for {args.entity_type.replace('_', '-')} {args.industry} companies")
    print(f"[Output] Results saved in {output_dir}")
    
    # Summary of results
    successful = len([r for r in results.values() if not isinstance(r, dict) or "error" not in r])
    print(f"[Summary] {successful}/{len(results)} states processed successfully")

if __name__ == "__main__":
    main() 