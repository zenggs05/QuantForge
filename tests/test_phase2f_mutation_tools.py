"""Exact-copy mutation tooling unit tests; no subprocess or Git calls."""
import pytest
from tools.mutation_check.apply_mutant import apply_mutant


def test_mutation_preserves_crlf_and_unrelated_source():
    source = b'"""Synthetic."""\r\nZ975 = 1.95996398454\r\nunchanged = 4\r\n'
    changed = apply_mutant('h3', source)
    assert changed == b'"""Synthetic."""\r\nZ975 = 1.645\r\nunchanged = 4\r\n'


@pytest.mark.parametrize('source',[b'x=1\n',b'Z975 = 1.95996398454\nZ975 = 1.95996398454\n'])
def test_mutation_refuses_missing_or_ambiguous_source(source):
    with pytest.raises(ValueError,match='not unique'):
        apply_mutant('h3',source)


def test_mutation_rejects_unregistered_target():
    with pytest.raises(ValueError,match='Unknown'):
        apply_mutant('unknown',b'x=1\n')
