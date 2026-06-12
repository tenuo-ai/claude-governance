"""make_constraint / make_web_constraints — constraint construction.

Needs the `tenuo` + `tenuo_core` constraint types (installed deps); skipped if
the native extension isn't importable.
"""
from __future__ import annotations

import pytest

tenuo = pytest.importorskip("tenuo")
tenuo_core = pytest.importorskip("tenuo_core")


def test_make_constraint_each_kind(cli_mod):
    from tenuo import Exact, OneOf, Pattern, Regex, Subpath
    from tenuo_core import Shlex

    assert isinstance(cli_mod.make_constraint("subpath:/sb", "/sb"), Subpath)
    assert isinstance(cli_mod.make_constraint("shlex:ls,echo", "/sb"), Shlex)
    assert isinstance(cli_mod.make_constraint("regex:^x$", "/sb"), Regex)
    assert isinstance(cli_mod.make_constraint("pattern:*.py", "/sb"), Pattern)
    assert isinstance(cli_mod.make_constraint("oneof:a,b", "/sb"), OneOf)
    assert isinstance(cli_mod.make_constraint("exact:v", "/sb"), Exact)


def test_make_constraint_sandbox_substitution(cli_mod):
    # {sandbox} must expand; constructing the Subpath should not raise.
    from tenuo import Subpath
    c = cli_mod.make_constraint("subpath:{sandbox}/sub", "/abs/sandbox")
    assert isinstance(c, Subpath)


def test_make_constraint_unknown_kind_raises(cli_mod):
    with pytest.raises(SystemExit):
        cli_mod.make_constraint("bogus:whatever", "/sb")


def test_make_web_constraints_single_domain(cli_mod):
    from tenuo_core import Pattern, UrlSafe
    cons = cli_mod.make_web_constraints({"domains": ["api.github.com"]})
    assert isinstance(cons["url"], UrlSafe)
    assert isinstance(cons["host"], Pattern)   # single member -> not wrapped in AnyOf


def test_make_web_constraints_multi_domain_anyof(cli_mod):
    from tenuo_core import AnyOf
    cons = cli_mod.make_web_constraints({"domains": ["a.com", "b.com"]})
    assert isinstance(cons["host"], AnyOf)


def test_make_web_constraints_approval_gate_wildcard_host(cli_mod):
    from tenuo_core import UrlSafe, Wildcard
    cons = cli_mod.make_web_constraints({"domains": ["a.com"]}, approval_gate=True)
    assert isinstance(cons["url"], UrlSafe)
    assert isinstance(cons["host"], Wildcard)   # domain policy moves to the Cloud gate


def test_make_web_constraints_requires_domain_or_cidr(cli_mod):
    with pytest.raises(SystemExit):
        cli_mod.make_web_constraints({})
