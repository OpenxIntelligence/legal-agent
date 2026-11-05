"""Search a bounded official publication index without provider credentials."""
from .. import public_sources
DEFINITION = {
    "type": "function", "name": "search_public_sources",
    "description": "Search official Federal Register notices/rules (not the entire web or caselaw). Use short public legal terms without private names or secrets. Returns URLs, not verified evidence: fetch a relevant source before citing. At most 6 searches per run; disclose gaps if inaccessible.",
    "parameters": {"type": "object", "additionalProperties": False,
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 5}}, "required": ["query"]},
}
def run(ctx, args):
    return public_sources.search(ctx.case, args.get("query", ""), args.get("limit", 5))
