"""Small argument guards for wrappers whose coercions could change meaning."""
from ..case_record import CaseError


def boolean_arg(tool: str, args: dict, field: str, default: bool = False) -> bool:
    value = args.get(field, default)
    if not isinstance(value, bool):
        raise CaseError(f"{tool} {field} must be a boolean (true or false)")
    return value


def text_arg(tool: str, args: dict, field: str, default: str = "") -> str:
    value = args.get(field, default)
    if not isinstance(value, str):
        raise CaseError(f"{tool} {field} must be text")
    return value
