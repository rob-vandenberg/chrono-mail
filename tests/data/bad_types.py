# Deliberately wrong code. Used only by tests/test_type_strictness.py to prove
# that Pyrefly strict mode reports errors. Excluded from the project check.
x: int = "a"


def unannotated(value):
    return value
