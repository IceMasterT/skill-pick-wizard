from skillpick.frontmatter import parse_frontmatter as p


def test_plain_scalars():
    assert p("---\nname: tdd\ndescription: Use when testing\n---\nbody") == {"name": "tdd", "description": "Use when testing"}


def test_crlf_and_bom():
    assert p("﻿---\r\nname: repo-scan\r\ndescription: Audit code\r\n---\r\n")["description"] == "Audit code"


def test_quoted_with_colon():
    assert p('---\ndescription: "Use when: auditing"\n---\n')["description"] == "Use when: auditing"
    assert p("---\ndescription: 'It''s fine'\n---\n")["description"] == "It's fine"


def test_folded_and_literal_blocks_joined_to_one_line():
    t = "---\ndescription: >-\n  Line one\n  line two\nname: x\n---\n"
    assert p(t) == {"description": "Line one line two", "name": "x"}
    assert p("---\ndescription: |\n  a\n  b\n---\n")["description"] == "a b"


def test_multiline_plain_continuation():
    assert p("---\ndescription: first part\n  second part\n---\n")["description"] == "first part second part"


def test_lists_inline_and_block():
    assert p('---\ntags: [a, "b c", tool/x]\n---\n')["tags"] == ["a", "b c", "tool/x"]
    assert p("---\ntags:\n- a\n- tool/security/ti\n---\n")["tags"] == ["a", "tool/security/ti"]


def test_no_frontmatter_returns_empty():
    assert p("# Just a heading\n") == {}
    assert p("---\nname: x\n") == {}   # unterminated


def test_nested_mapping_values_are_ignored_not_crashing():
    assert p("---\nname: x\nmetadata:\n  a: 1\n---\n")["name"] == "x"
