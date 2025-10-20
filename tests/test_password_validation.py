"""Unit tests for password validation improvements"""

import pytest

from gefapi.validators import validate_password


class TestPasswordValidation:
    """Test enhanced password validation rules"""

    def test_password_minimum_length(self):
        """Test password must be at least 12 characters"""
        # Too short
        with pytest.raises(ValueError, match="must be at least 12 characters"):
            validate_password("Short1!")

        # Exactly 12 characters (should pass other validations too)
        result = validate_password("ValidPass1!@")
        assert result == "ValidPass1!@"

    def test_password_maximum_length(self):
        """Test password must not exceed 128 characters (DoS protection)"""
        # Exactly 128 characters (should work)
        valid_long = "A1!" + "x" * 125  # 128 chars total
        result = validate_password(valid_long)
        assert result == valid_long

        # 129 characters (too long)
        too_long = "A1!" + "x" * 126  # 129 chars total
        with pytest.raises(ValueError, match="must not exceed 128 characters"):
            validate_password(too_long)

        # Way too long (prevents DoS)
        way_too_long = "A1!" + "x" * 1000
        with pytest.raises(ValueError, match="must not exceed 128 characters"):
            validate_password(way_too_long)

    def test_password_requires_uppercase(self):
        """Test password must contain at least one uppercase letter"""
        with pytest.raises(
            ValueError, match="must contain at least one uppercase letter"
        ):
            validate_password("alllowercase123!")

    def test_password_requires_lowercase(self):
        """Test password must contain at least one lowercase letter"""
        with pytest.raises(
            ValueError, match="must contain at least one lowercase letter"
        ):
            validate_password("ALLUPPERCASE123!")

    def test_password_requires_digit(self):
        """Test password must contain at least one digit"""
        with pytest.raises(ValueError, match="must contain at least one digit"):
            validate_password("NoDigitsHere!")

    def test_password_requires_special_character(self):
        """Test password must contain at least one special character"""
        with pytest.raises(
            ValueError, match="must contain at least one special character"
        ):
            validate_password("NoSpecialChar123")

    def test_password_empty_string(self):
        """Test empty password is rejected"""
        with pytest.raises(ValueError, match="Password is required"):
            validate_password("")

    def test_password_none(self):
        """Test None password is rejected"""
        with pytest.raises(ValueError, match="Password is required"):
            validate_password(None)

    def test_valid_passwords(self):
        """Test various valid passwords that meet all requirements"""
        valid_passwords = [
            "MySecurePass123!",
            "Tr3nd$Earth2024",
            "C0mpl3x&Secure",
            "UPPER lower 123 !@#",
            "Pass123!@#Word",
            "Te$t1ngSecur1ty",
            "Admin@2024Pass",
            "12345Abcde!@#$%",
        ]

        for password in valid_passwords:
            result = validate_password(password)
            assert result == password

    def test_password_with_various_special_characters(self):
        """Test passwords with different special characters are accepted"""
        special_chars = r"!@#$%^&*(),.?\":{}|<>-_+=[]\/;'`~"
        for char in special_chars:
            password = f"ValidPass123{char}"
            result = validate_password(password)
            assert result == password

    def test_dos_protection_realistic_attack(self):
        """Test that extremely long passwords are rejected (DoS protection)"""
        # Simulate DoS attack with 10MB password
        massive_password = "A1!" + "x" * (10 * 1024 * 1024)
        with pytest.raises(ValueError, match="must not exceed 128 characters"):
            validate_password(massive_password)

    def test_password_edge_cases(self):
        """Test edge cases and boundary conditions"""
        # Exactly at minimum length with all requirements
        assert validate_password("Abcdefgh123!") == "Abcdefgh123!"

        # Just above minimum
        assert validate_password("Abcdefgh1234!") == "Abcdefgh1234!"

        # At maximum length
        max_valid = "A1!" + "b" * 124 + "!"  # 128 chars, meets all requirements
        assert validate_password(max_valid) == max_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
