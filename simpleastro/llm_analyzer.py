"""LLM integration module for chart analysis and report generation.

Provides interface with local LLM (Ollama) for generating comprehensive astrology reports.
Handles prompt construction, LLM communication, and graceful fallbacks.
"""
import json
import logging
import os
from typing import Any, Dict, Generator, Optional

import requests

logger = logging.getLogger(__name__)

# Public API
__all__ = [
    "initialize_llm",
    "load_analysis_instructions",
    "build_analysis_prompt",
    "analyze_chart",
    "stream_analysis",
]

# Module-level cache for instructions
_instructions_cache: Optional[str] = None


def _get_llm_config() -> Dict[str, Any]:
    """Retrieve LLM configuration from environment variables.

    Returns:
        Dictionary with keys: model, base_url, temperature, max_tokens, timeout

    Raises:
        ValueError: If configuration values are invalid
    """
    timeout = int(os.getenv("LLM_TIMEOUT", "3000"))
    if timeout <= 0:
        raise ValueError("LLM_TIMEOUT must be positive")

    temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    if not 0.0 <= temperature <= 2.0:
        logger.warning(f"LLM_TEMPERATURE {temperature} is outside typical range [0.0, 2.0]")

    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "100000"))
    if max_tokens <= 0:
        raise ValueError("LLM_MAX_TOKENS must be positive")

    return {
        "model": os.getenv("LLM_MODEL", "qwen3:4b"),
        "base_url": os.getenv("LLM_BASE_URL", "http://localhost:11434"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }


def initialize_llm() -> bool:
    """Initialize LLM connection and verify model availability.

    Attempts to contact the Ollama API and confirm the configured model is available.

    Returns:
        True if LLM is available and ready, False otherwise
    """
    config = _get_llm_config()
    api_url = f"{config['base_url']}/api/tags"

    try:
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        model_names = [m.get("name", "") for m in models]

        # Check if configured model is available (allow for version variations)
        configured_model = config["model"]
        is_available = any(configured_model in name for name in model_names)

        if is_available:
            logger.info(f"LLM initialized: {configured_model} available at {config['base_url']}")
            return True
        else:
            logger.warning(
                f"Configured model '{configured_model}' not found. Available models: {model_names}"
            )
            return False
    except requests.exceptions.ConnectionError:
        logger.error(
            f"Could not connect to LLM at {config['base_url']}. "
            "Ensure Ollama is running."
        )
        return False
    except Exception as e:
        logger.error(f"LLM initialization failed: {e}")
        return False


def load_analysis_instructions(instructions_path: Optional[str] = None) -> str:
    """Load chart analysis instructions from markdown file.

    Caches instructions in memory after first load for performance.

    Args:
        instructions_path: Path to chart_analysis_instructions.md.
                          If None, searches relative to this module.

    Returns:
        String contents of the instructions file

    Raises:
        FileNotFoundError: If instructions file cannot be found
    """
    global _instructions_cache

    if _instructions_cache is not None:
        return _instructions_cache

    if instructions_path is None:
        # Default path relative to this module
        module_dir = os.path.dirname(os.path.abspath(__file__))
        instructions_path = os.path.join(module_dir, "..", "chart_analysis_instructions.md")

    instructions_path = os.path.abspath(instructions_path)

    if not os.path.exists(instructions_path):
        raise FileNotFoundError(f"Chart analysis instructions not found at: {instructions_path}")

    with open(instructions_path, "r", encoding="utf-8") as f:
        _instructions_cache = f.read()

    logger.info(f"Loaded analysis instructions from {instructions_path}")
    return _instructions_cache


def build_analysis_prompt(
    chart_data: Dict[str, Any],
    user_preferences: Optional[str] = None,
    instructions: Optional[str] = None
) -> str:
    """Construct a comprehensive analysis prompt for the LLM.

    Combines chart data, instructions, and optional user preferences into
    a structured prompt suitable for natal chart analysis.

    Args:
        chart_data: Extracted chart data from chart_extractor.extract_chart_data()
        user_preferences: Optional string with user-specified focus areas
        instructions: Optional pre-loaded instructions (will load if not provided)

    Returns:
        Complete prompt string for LLM analysis
    """
    if instructions is None:
        instructions = load_analysis_instructions()

    # Format chart data as readable JSON
    chart_json = json.dumps(chart_data, indent=2)

    # Build prompt
    prompt = f"""You are an expert astrologer providing a comprehensive natal chart analysis.

Use the following analysis guidelines to structure your response:

---
{instructions}
---

Here is the natal chart data for analysis:

```json
{chart_json}
```

"""

    if user_preferences:
        prompt += f"""The user has requested focus on the following areas:
{user_preferences}

"""

    prompt += """Provide a comprehensive, detailed analysis of this natal chart. Be specific with 
planetary placements, house positions, aspects, and patterns. Weave the individual 
elements into a coherent narrative about the person's psychology, life direction, 
strengths, and growth areas. Use clear formatting with section headers for easy reading."""

    return prompt


def analyze_chart(
    chart_data: Dict[str, Any],
    user_preferences: Optional[str] = None,
    instructions: Optional[str] = None
) -> str:
    """Generate a full report by analyzing chart data with the local LLM.

    Blocks until analysis completes. For long-running analysis, consider
    using stream_analysis() instead.

    Args:
        chart_data: Extracted chart data from chart_extractor.extract_chart_data()
        user_preferences: Optional string with user-specified focus areas
        instructions: Optional pre-loaded instructions (will load if not provided)

    Returns:
        Complete analysis report as string

    Raises:
        ConnectionError: If LLM is unavailable
        TimeoutError: If LLM request exceeds timeout
        Exception: For other LLM errors
    """
    config = _get_llm_config()
    prompt = build_analysis_prompt(chart_data, user_preferences, instructions)

    api_url = f"{config['base_url']}/api/generate"

    payload = {
        "model": config["model"],
        "prompt": prompt,
        "stream": False,
        "temperature": config["temperature"],
        "num_predict": config["max_tokens"],
    }

    try:
        logger.info(f"Sending analysis request to LLM ({config['model']})")
        response = requests.post(
            api_url,
            json=payload,
            timeout=config["timeout"]
        )
        response.raise_for_status()

        result = response.json()
        report = result.get("response", "")

        if not report:
            raise ValueError("LLM returned empty response")

        logger.info("Analysis completed successfully")
        return report

    except requests.exceptions.Timeout:
        logger.error(f"LLM request timed out after {config['timeout']} seconds")
        raise TimeoutError(
            f"Analysis timed out after {config['timeout']} seconds. "
            "The LLM may be processing a complex chart or the timeout is too short."
        )
    except requests.exceptions.ConnectionError:
        logger.error(f"Could not connect to LLM at {config['base_url']}")
        raise ConnectionError(
            f"Local LLM service not available at {config['base_url']}. "
            "Please ensure Ollama is running."
        )
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        raise


def stream_analysis(
    chart_data: Dict[str, Any],
    user_preferences: Optional[str] = None,
    instructions: Optional[str] = None
) -> Generator[str, None, None]:
    """Generate analysis with streaming output for real-time UI updates.

    Yields report content as it becomes available from the LLM. Useful for
    displaying progress to users during long-running analysis.

    Args:
        chart_data: Extracted chart data from chart_extractor.extract_chart_data()
        user_preferences: Optional string with user-specified focus areas
        instructions: Optional pre-loaded instructions (will load if not provided)

    Yields:
        Chunks of analysis text as they arrive from LLM

    Raises:
        ConnectionError: If LLM is unavailable
        TimeoutError: If LLM request exceeds timeout
        Exception: For other LLM errors
    """
    config = _get_llm_config()
    prompt = build_analysis_prompt(chart_data, user_preferences, instructions)

    api_url = f"{config['base_url']}/api/generate"

    payload = {
        "model": config["model"],
        "prompt": prompt,
        "stream": True,
        "temperature": config["temperature"],
        "num_predict": config["max_tokens"],
    }

    try:
        logger.info(f"Sending streaming analysis request to LLM ({config['model']})")
        response = requests.post(
            api_url,
            json=payload,
            stream=True,
            timeout=config["timeout"]
        )
        response.raise_for_status()

        for line in response.iter_lines():
            if line:
                try:
                    chunk = json.loads(line)
                    text = chunk.get("response", "")
                    if text:
                        yield text
                except json.JSONDecodeError as e:
                    logger.debug(f"Skipped malformed JSON line from LLM: {e}")
                    continue

        logger.info("Streaming analysis completed successfully")

    except requests.exceptions.Timeout:
        logger.error(f"LLM streaming request timed out after {config['timeout']} seconds")
        raise TimeoutError(
            f"Analysis timed out after {config['timeout']} seconds. "
            "The LLM may be processing a complex chart or the timeout is too short."
        )
    except requests.exceptions.ConnectionError:
        logger.error(f"Could not connect to LLM at {config['base_url']}")
        raise ConnectionError(
            f"Local LLM service not available at {config['base_url']}. "
            "Please ensure Ollama is running."
        )
    except Exception as e:
        logger.error(f"LLM streaming analysis failed: {e}")
        raise

