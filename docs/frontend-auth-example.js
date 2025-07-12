// Example front-end implementation for refresh tokens

class AuthService {
    constructor() {
        this.baseURL = 'https://api.trends.earth';
        this.accessToken = localStorage.getItem('access_token');
        this.refreshToken = localStorage.getItem('refresh_token');
    }

    async login(email, password) {
        try {
            const response = await fetch(`${this.baseURL}/auth`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ email, password }),
            });

            if (response.ok) {
                const data = await response.json();
                
                // Store tokens
                this.accessToken = data.access_token;
                this.refreshToken = data.refresh_token;
                localStorage.setItem('access_token', this.accessToken);
                localStorage.setItem('refresh_token', this.refreshToken);
                
                return data;
            } else {
                throw new Error('Invalid credentials');
            }
        } catch (error) {
            console.error('Login failed:', error);
            throw error;
        }
    }

    async refreshAccessToken() {
        if (!this.refreshToken) {
            throw new Error('No refresh token available');
        }

        try {
            const response = await fetch(`${this.baseURL}/auth/refresh`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ refresh_token: this.refreshToken }),
            });

            if (response.ok) {
                const data = await response.json();
                
                // Update access token
                this.accessToken = data.access_token;
                localStorage.setItem('access_token', this.accessToken);
                
                return data.access_token;
            } else {
                // Refresh token is invalid, need to login again
                this.logout();
                throw new Error('Refresh token expired');
            }
        } catch (error) {
            console.error('Token refresh failed:', error);
            throw error;
        }
    }

    async makeAuthenticatedRequest(url, options = {}) {
        // Add authorization header
        options.headers = {
            ...options.headers,
            'Authorization': `Bearer ${this.accessToken}`,
        };

        try {
            let response = await fetch(url, options);

            // If access token expired, try to refresh
            if (response.status === 401) {
                await this.refreshAccessToken();
                
                // Retry the original request with new token
                options.headers['Authorization'] = `Bearer ${this.accessToken}`;
                response = await fetch(url, options);
            }

            return response;
        } catch (error) {
            console.error('Authenticated request failed:', error);
            throw error;
        }
    }

    async logout() {
        try {
            // Revoke refresh token on server
            if (this.refreshToken) {
                await fetch(`${this.baseURL}/auth/logout`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${this.accessToken}`,
                    },
                    body: JSON.stringify({ refresh_token: this.refreshToken }),
                });
            }
        } catch (error) {
            console.error('Logout API call failed:', error);
        } finally {
            // Clear local tokens regardless of API call success
            this.accessToken = null;
            this.refreshToken = null;
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
        }
    }

    async logoutFromAllDevices() {
        try {
            await fetch(`${this.baseURL}/auth/logout-all`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.accessToken}`,
                },
            });
        } catch (error) {
            console.error('Logout from all devices failed:', error);
        } finally {
            this.logout(); // Clear local tokens
        }
    }

    async getUserSessions() {
        try {
            const response = await this.makeAuthenticatedRequest(
                `${this.baseURL}/api/v1/user/me/sessions`
            );
            
            if (response.ok) {
                return await response.json();
            } else {
                throw new Error('Failed to fetch user sessions');
            }
        } catch (error) {
            console.error('Get user sessions failed:', error);
            throw error;
        }
    }

    async revokeSession(sessionId) {
        try {
            const response = await this.makeAuthenticatedRequest(
                `${this.baseURL}/api/v1/user/me/sessions/${sessionId}`,
                { method: 'DELETE' }
            );
            
            if (response.ok) {
                return await response.json();
            } else {
                throw new Error('Failed to revoke session');
            }
        } catch (error) {
            console.error('Revoke session failed:', error);
            throw error;
        }
    }
}

// Usage example
const authService = new AuthService();

// Login
authService.login('user@example.com', 'password')
    .then(() => console.log('Logged in successfully'))
    .catch(error => console.error('Login failed:', error));

// Make API requests (automatically handles token refresh)
authService.makeAuthenticatedRequest('/api/v1/user/me')
    .then(response => response.json())
    .then(data => console.log('User profile:', data))
    .catch(error => console.error('API request failed:', error));
