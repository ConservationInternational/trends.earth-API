#!/bin/bash
# Script to generate SRI hashes for Swagger UI CDN resources

set -e

SWAGGER_VERSION="4.15.5"
CSS_URL="https://unpkg.com/swagger-ui-dist@${SWAGGER_VERSION}/swagger-ui.css"
JS_URL="https://unpkg.com/swagger-ui-dist@${SWAGGER_VERSION}/swagger-ui-bundle.js"

echo "Generating SRI hashes for Swagger UI version ${SWAGGER_VERSION}"
echo "==========================================================="

echo "Downloading and hashing CSS file..."
CSS_HASH=$(curl -s "$CSS_URL" | openssl dgst -sha384 -binary | openssl base64 -A)
echo "CSS SRI: sha384-${CSS_HASH}"

echo ""

echo "Downloading and hashing JS file..."
JS_HASH=$(curl -s "$JS_URL" | openssl dgst -sha384 -binary | openssl base64 -A)
echo "JS SRI:  sha384-${JS_HASH}"

echo ""
echo "HTML snippets for copy-paste:"
echo "============================="
echo ""
echo "CSS:"
echo "<link rel=\"stylesheet\" type=\"text/css\""
echo "      href=\"${CSS_URL}\""
echo "      integrity=\"sha384-${CSS_HASH}\""
echo "      crossorigin=\"anonymous\" />"
echo ""
echo "JS:"
echo "<script src=\"${JS_URL}\""
echo "        integrity=\"sha384-${JS_HASH}\""
echo "        crossorigin=\"anonymous\"></script>"

# Optional: Update the Python file automatically
echo ""
echo "Would you like to update gefapi/__init__.py automatically? (y/n)"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo "Updating gefapi/__init__.py..."
    # This would require more sophisticated text replacement
    echo "Manual update recommended - copy the hashes above into the HTML template"
fi
