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
    r = p("---\nname: x\nmetadata:\n  a: 1\n---\n")
    assert r["name"] == "x"
    assert "metadata" not in r


def test_crlf_folded_block():
    assert p("---\r\ndescription: >-\r\n  Line one\r\n  line two\r\nname: x\r\n---\r\n") == {"description": "Line one line two", "name": "x"}


def test_crlf_multiline_plain():
    assert p("---\r\ndescription: first\r\n  second\r\n---\r\n")["description"] == "first second"


def test_blank_line_inside_block():
    assert p("---\ndescription: |\n  a\n\n  b\nname: x\n---\n") == {"description": "a b", "name": "x"}


def test_value_on_next_indented_line():
    assert p("---\ndescription:\n  Use when testing\nname: x\n---\n") == {"description": "Use when testing", "name": "x"}


def test_indented_dashes_inside_block_do_not_terminate():
    assert p("---\ndescription: |\n  a\n  ---\n  b\nname: x\n---\n") == {"description": "a --- b", "name": "x"}


def test_hash_line_inside_block_is_content():
    assert p("---\ndescription: |\n  # Heading\n  text\n---\n")["description"] == "# Heading text"


def test_hash_comment_lines_outside_blocks_skipped():
    assert p("---\n# top\nname: x\ndescription: a\n  # inner\n  b\n---\n") == {"name": "x", "description": "a b"}


def test_double_quote_escapes():
    assert p('---\ndescription: "a\\nb\\tc \\"q\\" \\\\ \\/ \\u00e9 \\x41"\n---\n')["description"] == 'a\nb\tc "q" \\ / \u00e9 A'
    assert p("---\ndescription: 'a\\nb'\n---\n")["description"] == "a\\nb"


def test_quoted_with_trailing_comment():
    assert p("---\nname: 'x' # c\n---\n")["name"] == "x"
    assert p('---\nname: "x y"   # c\n---\n')["name"] == "x y"
    assert p("---\ntags:\n- 'a' # c\n---\n")["tags"] == ["a"]


def test_plain_comment_rules():
    assert p("---\nname: x # c\n---\n")["name"] == "x"
    assert p("---\nname: a#b\n---\n")["name"] == "a#b"
    assert p("---\ndescription: |  # c\n  a\n---\n")["description"] == "a"


def test_multiline_inline_list():
    assert p("---\ntags: [a,\n  b, 'c, d']\nname: x\n---\n") == {"tags": ["a", "b", "c, d"], "name": "x"}
