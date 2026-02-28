import Foundation
import AuthenticationServices

class AuthService: ObservableObject {
    static let shared = AuthService()

    @Published var isAuthenticated = false
    @Published var currentUser: Rider?
    @Published var authToken: String?

    private let defaults = UserDefaults.standard
    private let tokenKey = "auth_token"
    private let userKey = "current_user"

    private init() {
        loadAuthState()
    }

    // MARK: - Auth State Management

    private func loadAuthState() {
        if let token = defaults.string(forKey: tokenKey),
           let userData = defaults.data(forKey: userKey),
           let user = try? JSONDecoder().decode(Rider.self, from: userData) {
            self.authToken = token
            self.currentUser = user
            self.isAuthenticated = true
        }
    }

    func saveAuthState(token: String, user: Rider) {
        defaults.set(token, forKey: tokenKey)
        if let userData = try? JSONEncoder().encode(user) {
            defaults.set(userData, forKey: userKey)
        }
        self.authToken = token
        self.currentUser = user
        self.isAuthenticated = true
    }

    func clearAuthState() {
        defaults.removeObject(forKey: tokenKey)
        defaults.removeObject(forKey: userKey)
        self.authToken = nil
        self.currentUser = nil
        self.isAuthenticated = false
    }

    // MARK: - Sign In with Google

    func signInWithGoogle(presentingViewController: UIViewController) async throws {
        // Note: This requires GoogleSignIn SDK integration
        // For now, this is a placeholder for the OAuth flow
        // The actual implementation would use Google Sign-In SDK

        // TODO: Implement Google Sign-In flow
        // 1. Present Google Sign-In
        // 2. Get ID token
        // 3. Send to backend /auth/google/callback
        // 4. Receive session token
        // 5. Save auth state

        throw AuthError.notImplemented
    }

    // MARK: - Sign Out

    func signOut() {
        clearAuthState()
    }

    // MARK: - Demo/Test Login (for development)

    func demoLogin(email: String) async throws {
        // For demo purposes - in production, remove this
        let demoUser = Rider(
            id: 1,
            name: "Demo User",
            email: email,
            photoUrl: nil,
            googleId: nil,
            createdAt: nil
        )
        let demoToken = "demo_token_\(UUID().uuidString)"
        saveAuthState(token: demoToken, user: demoUser)
    }
}

// MARK: - Auth Error

enum AuthError: LocalizedError {
    case notImplemented
    case cancelled
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .notImplemented:
            return "Authentication not yet implemented"
        case .cancelled:
            return "Sign in cancelled"
        case .failed(let message):
            return "Sign in failed: \(message)"
        }
    }
}
