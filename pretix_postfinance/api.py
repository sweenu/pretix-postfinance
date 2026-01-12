"""
PostFinance Checkout API client.

Provides a client for communicating with the PostFinance Checkout REST API
using JWT authentication with HMAC-SHA256 signing.
"""

import base64
import json
import logging
import time
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlparse

import jwt
import requests

logger = logging.getLogger(__name__)


class PostFinanceError(Exception):
    """Base exception for PostFinance API errors."""

    def __init__(self, message: str, response: Optional[requests.Response] = None) -> None:
        super().__init__(message)
        self.message = message
        self.response = response


class PostFinanceClient:
    """
    Client for PostFinance Checkout API.

    Implements JWT authentication with HMAC-SHA256 signing per PostFinance specification.

    Attributes:
        space_id: The PostFinance space ID.
        user_id: The PostFinance user ID for authentication.
        api_secret: The base64-encoded API secret key.
        environment: Either 'sandbox' or 'production'.
    """

    PRODUCTION_URL = "https://checkout.postfinance.ch"
    # PostFinance doesn't have a separate sandbox URL - the same endpoint is used
    # with sandbox/test space configurations
    SANDBOX_URL = "https://checkout.postfinance.ch"

    API_VERSION = "v2.0"
    DEFAULT_TIMEOUT = 30  # seconds

    def __init__(
        self,
        space_id: int,
        user_id: int,
        api_secret: str,
        environment: Literal["sandbox", "production"] = "production",
    ) -> None:
        """
        Initialize the PostFinance API client.

        Args:
            space_id: The PostFinance space ID.
            user_id: The PostFinance user ID for authentication.
            api_secret: The base64-encoded API secret key.
            environment: Either 'sandbox' or 'production'. Defaults to 'production'.
        """
        self.space_id = space_id
        self.user_id = user_id
        self.api_secret = api_secret
        self.environment = environment
        self._session = requests.Session()

    @property
    def base_url(self) -> str:
        """
        Get the base URL for API requests based on the environment.

        Returns:
            The base URL string for the configured environment.
        """
        if self.environment == "sandbox":
            return f"{self.SANDBOX_URL}/api/{self.API_VERSION}"
        return f"{self.PRODUCTION_URL}/api/{self.API_VERSION}"

    def _build_jwt_token(self, method: str, path: str) -> str:
        """
        Build a JWT token for authenticating a request.

        The JWT is signed using HMAC-SHA256 with the base64-decoded API secret.

        Args:
            method: The HTTP method (GET, POST, etc.).
            path: The API path (e.g., /space/read).

        Returns:
            The signed JWT token string.
        """
        # Decode the base64-encoded secret
        decoded_secret = base64.b64decode(self.api_secret)

        # Build the JWT payload
        payload = {
            "sub": self.user_id,
            "iat": int(time.time()),
            "requestPath": path,
            "requestMethod": method.upper(),
        }

        # Custom headers per PostFinance spec
        headers = {
            "alg": "HS256",
            "typ": "JWT",
            "ver": 1,
        }

        # Sign and return the token
        token: str = jwt.encode(payload, decoded_secret, algorithm="HS256", headers=headers)
        return token

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Make an authenticated request to the PostFinance API.

        Args:
            method: The HTTP method (GET, POST, etc.).
            path: The API path (without base URL).
            params: Optional query parameters.
            data: Optional JSON body data for POST/PUT requests.
            timeout: Optional request timeout in seconds.

        Returns:
            The JSON response as a dictionary.

        Raises:
            PostFinanceError: If the request fails or returns an error response.
        """
        url = f"{self.base_url}{path}"
        parsed_url = urlparse(url)
        request_path = parsed_url.path

        # Generate authentication token
        token = self._build_jwt_token(method, request_path)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        request_timeout = timeout or self.DEFAULT_TIMEOUT

        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=data,
                headers=headers,
                timeout=request_timeout,
            )
        except requests.exceptions.Timeout as e:
            raise PostFinanceError(f"Request timed out after {request_timeout} seconds") from e
        except requests.exceptions.RequestException as e:
            raise PostFinanceError(f"Request failed: {e}") from e

        # Log request/response for debugging (without secrets)
        logger.debug(
            "PostFinance API request: %s %s -> %s",
            method.upper(),
            path,
            response.status_code,
        )

        if not response.ok:
            error_message = f"API request failed with status {response.status_code}"
            try:
                error_data = response.json()
                if "message" in error_data:
                    error_message = f"{error_message}: {error_data['message']}"
                logger.error(
                    "PostFinance API error: %s %s -> %s: %s",
                    method.upper(),
                    path,
                    response.status_code,
                    json.dumps(error_data),
                )
            except json.JSONDecodeError:
                logger.error(
                    "PostFinance API error: %s %s -> %s: %s",
                    method.upper(),
                    path,
                    response.status_code,
                    response.text[:500],
                )
            raise PostFinanceError(error_message, response)

        # Handle empty responses
        if not response.content:
            return {}

        try:
            result: Dict[str, Any] = response.json()
            return result
        except json.JSONDecodeError as e:
            raise PostFinanceError(f"Invalid JSON response: {response.text[:500]}") from e

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Make an authenticated GET request.

        Args:
            path: The API path (without base URL).
            params: Optional query parameters.
            timeout: Optional request timeout in seconds.

        Returns:
            The JSON response as a dictionary.
        """
        return self._request("GET", path, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Make an authenticated POST request.

        Args:
            path: The API path (without base URL).
            data: Optional JSON body data.
            params: Optional query parameters.
            timeout: Optional request timeout in seconds.

        Returns:
            The JSON response as a dictionary.
        """
        return self._request("POST", path, params=params, data=data, timeout=timeout)

    def get_space(self) -> Dict[str, Any]:
        """
        Get details about the configured space.

        This is useful for testing the connection and verifying credentials.

        Returns:
            The space details including id and name.

        Raises:
            PostFinanceError: If the request fails or credentials are invalid.
        """
        return self.get(f"/space/read?id={self.space_id}")
