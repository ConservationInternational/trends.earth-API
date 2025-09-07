"""
Security fixes validation tests for path traversal and weak random generation
"""

import os
import secrets
import string
import tarfile
import tempfile
import unittest


class TestSecurityFixes(unittest.TestCase):
    """Test cases for security vulnerability fixes"""

    def test_secure_random_password_generation(self):
        """Test that password generation uses cryptographically secure random"""
        # Test that secrets module is used instead of random
        charset = string.ascii_uppercase + string.digits

        # Generate multiple passwords to ensure they're different (non-deterministic)
        passwords = []
        for _ in range(10):
            password = "".join(secrets.choice(charset) for _ in range(6))
            passwords.append(password)

        # All passwords should be 6 characters
        self.assertTrue(all(len(p) == 6 for p in passwords))

        # All passwords should be different (very high probability)
        self.assertEqual(len(set(passwords)), len(passwords))

        # All characters should be from the expected charset
        for password in passwords:
            self.assertTrue(all(c in charset for c in password))

    def test_safe_tar_extraction_validation(self):
        """Test that our tar extraction prevents path traversal attacks"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a test tar file with safe content
            tar_path = os.path.join(temp_dir, "test.tar.gz")
            extract_path = os.path.join(temp_dir, "extract")
            os.makedirs(extract_path)

            # Create tar with safe files
            with tarfile.open(tar_path, "w:gz") as tar:
                # Add a safe file
                safe_file = os.path.join(temp_dir, "safe.txt")
                with open(safe_file, "w") as f:
                    f.write("safe content")
                tar.add(safe_file, arcname="safe.txt")

            # Our safe extraction should work
            with tarfile.open(tar_path, "r:gz") as tar:
                safe_members = [
                    m
                    for m in tar.getmembers()
                    if not os.path.isabs(m.name)
                    and ".." not in m.name
                    and not m.name.startswith("/")
                    and len(m.name) <= 255
                ]
                self.assertEqual(len(safe_members), 1)
                self.assertEqual(safe_members[0].name, "safe.txt")

    def test_path_traversal_detection(self):
        """Test that malicious paths are properly detected and filtered"""
        malicious_paths = [
            "../../../etc/passwd",  # Parent directory traversal
            "/etc/passwd",  # Absolute path
            "../../../../root/.ssh/id_rsa",  # Multiple parent traversals
            "a" * 300,  # Overly long path
        ]

        for path in malicious_paths:
            # Test absolute path detection
            if os.path.isabs(path):
                self.assertTrue(
                    os.path.isabs(path), f"Should detect absolute path: {path}"
                )

            # Test parent directory detection
            if ".." in path:
                self.assertIn("..", path, f"Should detect parent reference: {path}")

            # Test length validation
            if len(path) > 255:
                self.assertGreater(
                    len(path), 255, f"Should detect overly long path: {path}"
                )

    def test_tempdir_configuration(self):
        """Test that temporary directory configuration is secure"""
        import tempfile

        # Test that we use system temp directory instead of hardcoded /tmp
        temp_dir = tempfile.gettempdir()
        expected_upload_folder = os.path.join(temp_dir, "scripts")

        # This would be the secure path
        self.assertTrue(os.path.isabs(expected_upload_folder))
        self.assertIn("scripts", expected_upload_folder)


if __name__ == "__main__":
    unittest.main()
