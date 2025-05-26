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
    
    def analyze_tax_content(self, content: str, state_name: str, tax_type: str) -> Tuple[Dict[str, str], str]:
        """
        Analyze raw HTML/text content and extract tax rates using LLM
        
        Returns:
            - Dict: Extracted tax information
            - str: Reasoning process
        """
        if not self.available:
            return {}, "[LLM not available]"
        
        prompt = f"""
You are a tax analysis expert. Please analyze the following {state_name} state tax content for {tax_type} taxes.

CONTENT TO ANALYZE:
{content[:8000]}  # Limit content size

EXTRACTION REQUIREMENTS:
1. Find the standard corporate income tax rate (for general businesses)
2. Find any capital-based tax rates
3. Find minimum tax amounts or fixed dollar minimums
4. Identify any special rates for specific business types

OUTPUT FORMAT:
Please respond in JSON format with this structure:
{{
    "standard_rate": "X.XX% or rate description",
    "capital_rate": "X.XX% or N/A",
    "minimum_tax": "$X to $Y or description",
    "special_rates": ["list of special rates if any"],
    "reasoning": "Brief explanation of how you found these rates",
    "confidence": "high/medium/low",
    "source_sections": ["list of HTML sections or table names used"]
}}

Focus on finding rates that apply to "general business taxpayers" or "all other corporations".
If you cannot find specific information, mark it as "N/A" but explain why in the reasoning.
"""
        
        try:
            response = self.model.generate_content(prompt)
            analysis_text = response.text.strip()
            
            # Try to parse JSON response
            try:
                analysis_data = json.loads(analysis_text)
                reasoning = analysis_data.get("reasoning", "No reasoning provided")
                
                # Convert to our standard format
                result = {
                    "Entire Net Income Base": analysis_data.get("standard_rate", "N/A"),
                    "Business capital base": analysis_data.get("capital_rate", "N/A"),
                    "Fixed dollar minimum tax": analysis_data.get("minimum_tax", "N/A")
                }
                
                return result, f"--- {state_name} Analysis ---\n{reasoning}\nConfidence: {analysis_data.get('confidence', 'unknown')}"
                
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
            "corporate income"
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
        for state_code, result_data in self.results.items():
            config = result_data["config"]
            analysis = result_data["analysis"]
            
            # Format tax summary
            tax_summary = "; ".join([
                f"{k}: {v}" for k, v in analysis.items() if v != "N/A"
            ])
            
            row = [
                config.state_name,
                config.state_code,
                config.nexus_standard,
                config.nexus_effective_date,
                tax_summary,
                "",  # Tax rates (included in summary)
                config.tax_definitions_url,
                config.sales_factor_method,
                config.sales_factor_date
            ]
            ws.append(row)
        
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
    """Example usage of the multi-state framework"""
    
    # Setup - Load API key securely
    from config_loader import get_gemini_api_key, validate_config
    
    if not validate_config():
        print("[Error] Configuration validation failed!")
        return
    
    api_key = get_gemini_api_key()
    output_dir = Path("multi_state_output")
    
    # Create example configs
    create_example_state_configs()
    
    # Initialize extractor
    extractor = MultiStateTaxExtractor(api_key)
    
    # Process all states
    results = extractor.process_multiple_states(Path("state_configs"))
    
    # Export results
    extractor.export_results(output_dir)
    
    print("\n[Complete] Multi-state tax extraction finished!")
    print(f"Results saved in {output_dir}")

if __name__ == "__main__":
    main() 