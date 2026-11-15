"""The portable `where=` filter and the labels it matches."""

from __future__ import annotations

import pytest

from convilyn import rag
from convilyn.rag._filter import check_meta, parse_where


class TestParseWhere:
    def test_none_is_no_conditions(self) -> None:
        assert parse_where(None) == ()

    def test_an_empty_mapping_is_no_conditions(self) -> None:
        assert parse_where({}) == ()

    @pytest.mark.parametrize("value", ["en", 3, 2.5, True, False, ""])
    def test_a_value_is_one_condition_with_one_value(self, value: rag.Scalar) -> None:
        assert parse_where({"k": value}) == (rag.Condition(key="k", values=(value,)),)

    @pytest.mark.parametrize(
        "values",
        [["en", "ja"], ("en", "ja"), ["en"], [1, "1", True]],
        ids=["list", "tuple", "single", "mixed-types"],
    )
    def test_a_sequence_is_one_condition_with_its_values(self, values: object) -> None:
        assert parse_where({"k": values}) == (
            rag.Condition(key="k", values=tuple(values)),  # type: ignore[arg-type]
        )

    @pytest.mark.parametrize("values", [{"en"}, frozenset({"en"})], ids=["set", "frozenset"])
    def test_a_set_is_accepted(self, values: object) -> None:
        assert parse_where({"k": values}) == (rag.Condition(key="k", values=("en",)),)

    def test_several_keys_are_several_conditions_in_order(self) -> None:
        result = parse_where({"lang": "en", "team": ["a", "b"]})

        assert result == (
            rag.Condition(key="lang", values=("en",)),
            rag.Condition(key="team", values=("a", "b")),
        )

    def test_a_boolean_stays_a_boolean(self) -> None:
        (condition,) = parse_where({"k": True})

        assert condition.values[0] is True


class TestParseWhereErrors:
    @pytest.mark.parametrize(
        "wanted",
        [{"$gt": 3}, None, b"en", object()],
        ids=["operator-mapping", "none", "bytes", "object"],
    )
    def test_anything_but_a_value_or_a_list_is_refused(self, wanted: object) -> None:
        with pytest.raises(ValueError, match="not portable across stores"):
            parse_where({"k": wanted})

    def test_an_empty_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty list"):
            parse_where({"k": []})

    @pytest.mark.parametrize("item", [None, ["nested"], {"a": 1}], ids=["none", "list", "mapping"])
    def test_a_list_holding_something_else_is_refused(self, item: object) -> None:
        with pytest.raises(ValueError, match="may only list strings, numbers and booleans"):
            parse_where({"k": ["en", item]})

    @pytest.mark.parametrize("key", ["", 3, None], ids=["empty", "number", "none"])
    def test_a_name_that_is_not_a_non_empty_string_is_refused(self, key: object) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            parse_where({key: "en"})  # type: ignore[dict-item]

    @pytest.mark.parametrize("where", ["lang=en", [("lang", "en")], 3], ids=["str", "pairs", "int"])
    def test_something_that_is_not_a_mapping_is_refused(self, where: object) -> None:
        with pytest.raises(TypeError, match="where must be a mapping"):
            parse_where(where)  # type: ignore[arg-type]

    def test_a_very_long_name_is_not_echoed_in_full(self) -> None:
        with pytest.raises(ValueError) as caught:
            parse_where({"k" * 100_000: None})

        assert len(str(caught.value)) < 300


class TestCheckMeta:
    def test_none_is_no_labels(self) -> None:
        assert check_meta(None) == {}

    def test_labels_are_returned_as_a_new_dict(self) -> None:
        given = {"lang": "en", "year": 2024, "ratio": 0.5, "draft": False}

        result = check_meta(given)

        assert (result, result is given) == (given, False)

    @pytest.mark.parametrize(
        "value", [None, ["en"], {"a": 1}, b"en"], ids=["none", "list", "mapping", "bytes"]
    )
    def test_a_value_a_filter_could_not_match_is_refused(self, value: object) -> None:
        with pytest.raises(ValueError, match="must be a string, number or boolean"):
            check_meta({"k": value})  # type: ignore[dict-item]

    def test_an_empty_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            check_meta({"": "en"})

    def test_something_that_is_not_a_mapping_is_refused(self) -> None:
        with pytest.raises(TypeError, match="meta must be a mapping"):
            check_meta([("lang", "en")])  # type: ignore[arg-type]
