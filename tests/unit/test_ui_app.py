from client.ui.app import _clean_validation_error_message


def test_validation_error_prefix_cleanup_uses_exact_prefix_removal() -> None:
    assert (
        _clean_validation_error_message(
            "Value error, Password must be at least 12 characters"
        )
        == "Password must be at least 12 characters"
    )


def test_validation_error_prefix_cleanup_preserves_suffix_characters() -> None:
    assert (
        _clean_validation_error_message("Value error, Password error")
        == "Password error"
    )
