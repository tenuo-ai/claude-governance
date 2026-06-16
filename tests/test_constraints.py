"""make_constraint / make_web_constraints — constraint construction.

Needs the `tenuo` + `tenuo_core` constraint types (installed deps); skipped if
the native extension isn't importable.
"""
from __future__ import annotations

import pytest

tenuo = pytest.importorskip("tenuo")
tenuo_core = pytest.importorskip("tenuo_core")


def test_make_constraint_each_kind(cli_mod):
    from tenuo import (Cidr, Exact, NotOneOf, OneOf, Pattern, Range, Regex,
                       Subpath, UrlPattern)
    from tenuo_core import Shlex

    assert isinstance(cli_mod.make_constraint("subpath:/sb", "/sb"), Subpath)
    assert isinstance(cli_mod.make_constraint("shlex:ls,echo", "/sb"), Shlex)
    assert isinstance(cli_mod.make_constraint("regex:^x$", "/sb"), Regex)
    assert isinstance(cli_mod.make_constraint("pattern:*.py", "/sb"), Pattern)
    assert isinstance(cli_mod.make_constraint("oneof:a,b", "/sb"), OneOf)
    assert isinstance(cli_mod.make_constraint("notoneof:rm,curl", "/sb"), NotOneOf)
    assert isinstance(cli_mod.make_constraint("exact:v", "/sb"), Exact)
    assert isinstance(
        cli_mod.make_constraint("urlpattern:https://api.github.com/repos/*", "/sb"),
        UrlPattern)
    assert isinstance(cli_mod.make_constraint("cidr:10.0.0.0/8", "/sb"), Cidr)
    assert isinstance(cli_mod.make_constraint("range:0,10", "/sb"), Range)


@pytest.mark.parametrize("spec,ok", [
    ("range:0,10", True), ("range:0,", True),       # either bound may be blank
    ("range:,100", True), ("range:1.5,9.5", True),
    ("range:5", False), ("range:a,b", False),       # no comma / non-numeric
    ("range:,", False),                             # both blank -> match-all, rejected
])
def test_make_constraint_range(cli_mod, spec, ok):
    from tenuo import Range
    if ok:
        assert isinstance(cli_mod.make_constraint(spec, "/sb"), Range)
    else:
        with pytest.raises(SystemExit):
            cli_mod.make_constraint(spec, "/sb")


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


@pytest.mark.parametrize("ports,match", [
    (["nope"], "must be integers"),
    ([70000], "out of range"),
    ([0], "out of range"),
])
def test_make_web_constraints_rejects_bad_ports(cli_mod, ports, match):
    # Malformed ports must be a clear policy error, not a Python traceback.
    with pytest.raises(SystemExit, match=match):
        cli_mod.make_web_constraints({"domains": ["a.com"], "ports": ports})
