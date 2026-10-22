"""Tool schemas for the Jarvis browser (both brains). Calls go through the gatekeeper."""

BROWSER_TOOL_NAMES = ["browser_open", "browser_search", "browser_read", "browser_click",
                      "browser_type", "browser_scroll", "browser_back", "browser_tabs"]

BROWSER_SCHEMAS = [
    {"type": "function", "function": {
        "name": "browser_open",
        "description": "Open a web page in Jarvis's own visible Chrome window. Then use browser_read.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "e.g. https://example.com or example.com"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browser_search",
        "description": "Search the web in Jarvis's browser and return the results page (text + numbered elements).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "browser_read",
        "description": "Read the current page: its text plus numbered links, buttons and fields to use with "
                       "browser_click / browser_type. Call again after the page changes.",
        "parameters": {"type": "object", "properties": {
            "refresh": {"type": "boolean", "description": "Ignore the page cache"}}}}},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "Click a link or button by its number from browser_read (or by its exact label text).",
        "parameters": {"type": "object", "properties": {
            "index": {"type": "integer"},
            "text": {"type": "string", "description": "Label of the element, if no number"}}}}},
    {"type": "function", "function": {
        "name": "browser_type",
        "description": "Type into a field by its number from browser_read. Never use for passwords, card "
                       "numbers or one-time codes — the owner types those.",
        "parameters": {"type": "object", "properties": {
            "index": {"type": "integer"},
            "value": {"type": "string", "description": "Text to type"},
            "submit": {"type": "boolean", "description": "Press Enter afterwards"}},
            "required": ["index", "value"]}}},
    {"type": "function", "function": {
        "name": "browser_scroll",
        "description": "Scroll the current page.",
        "parameters": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "description": "Steps, default 3"}}}}},
    {"type": "function", "function": {
        "name": "browser_back",
        "description": "Go back to the previous page.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_tabs",
        "description": "List, open, switch or close tabs in Jarvis's browser.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "new", "switch", "close"]},
            "index": {"type": "integer", "description": "Tab number from list (switch/close)"}},
            "required": ["action"]}}},
]
