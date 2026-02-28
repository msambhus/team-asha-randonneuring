import Foundation

class APIClient: ObservableObject {
    static let shared = APIClient()

    // Base URL - can be configured for dev/prod
    private var baseURL = "https://team-asha-randonneuring.vercel.app"

    private init() {}

    func configure(baseURL: String? = nil) {
        if let baseURL = baseURL {
            self.baseURL = baseURL
        }
    }

    // MARK: - Generic Request Method

    private func request<T: Decodable>(
        endpoint: String,
        method: String = "GET",
        body: Data? = nil,
        authenticated: Bool = false
    ) async throws -> T {
        guard let url = URL(string: "\(baseURL)\(endpoint)") else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if authenticated, let token = AuthService.shared.authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        if let body = body {
            request.httpBody = body
        }

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            throw APIError.httpError(httpResponse.statusCode)
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            print("Decoding error: \(error)")
            throw APIError.decodingError(error)
        }
    }

    // MARK: - Brevets

    func fetchUpcomingBrevets() async throws -> [Brevet] {
        let response: UpcomingBrevetResponse = try await request(endpoint: "/api/brevets/upcoming")
        return response.brevets
    }

    // MARK: - Riders

    func fetchSeasonLeaderboard(seasonId: Int? = nil) async throws -> SeasonLeaderboard {
        let endpoint = seasonId != nil ? "/api/riders/season/\(seasonId!)" : "/api/riders"
        return try await request(endpoint: endpoint)
    }

    func fetchRiderProfile(riderId: Int) async throws -> RiderProfile {
        return try await request(endpoint: "/riders/\(riderId)")
    }

    // MARK: - Current User Profile

    func fetchMyProfile() async throws -> RiderProfile {
        return try await request(endpoint: "/api/profile/me", authenticated: true)
    }

    func updateProfile(name: String?, email: String?) async throws -> Rider {
        let body = ["name": name, "email": email].compactMapValues { $0 }
        let jsonData = try JSONEncoder().encode(body)
        return try await request(
            endpoint: "/api/profile/update",
            method: "POST",
            body: jsonData,
            authenticated: true
        )
    }

    // MARK: - Strava

    func syncStravaActivities() async throws -> SyncResponse {
        return try await request(
            endpoint: "/strava/sync",
            method: "POST",
            authenticated: true
        )
    }

    func fetchStravaActivities(riderId: Int, days: Int = 28) async throws -> [StravaActivity] {
        return try await request(endpoint: "/api/strava/activities/\(riderId)?days=\(days)")
    }

    // MARK: - Seasons

    func fetchSeasons() async throws -> [Season] {
        return try await request(endpoint: "/api/seasons")
    }
}

// MARK: - API Error

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpError(Int)
    case decodingError(Error)
    case unauthorized

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid URL"
        case .invalidResponse:
            return "Invalid response from server"
        case .httpError(let code):
            return "HTTP error: \(code)"
        case .decodingError(let error):
            return "Decoding error: \(error.localizedDescription)"
        case .unauthorized:
            return "Unauthorized - please sign in"
        }
    }
}

// MARK: - Helper Response Models

struct SyncResponse: Codable {
    let count: Int
    let message: String?
}
