import Foundation

struct Rider: Identifiable, Codable, Hashable {
    let id: Int
    let name: String
    let email: String?
    let photoUrl: String?
    let googleId: String?
    let createdAt: String?

    // Career stats
    var totalBrevets: Int?
    var totalDistance: Double?
    var isSuperRandonneur: Bool?
    var pbpFinisher: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, email
        case photoUrl = "photo_url"
        case googleId = "google_id"
        case createdAt = "created_at"
        case totalBrevets = "total_brevets"
        case totalDistance = "total_distance"
        case isSuperRandonneur = "is_super_randonneur"
        case pbpFinisher = "pbp_finisher"
    }
}

struct RiderProfile: Codable {
    let rider: Rider
    let seasonStats: [SeasonStats]
    let stravaConnection: StravaConnection?
    let eddingtonData: EddingtonData?
    let recentActivities: [StravaActivity]?

    enum CodingKeys: String, CodingKey {
        case rider
        case seasonStats = "season_stats"
        case stravaConnection = "strava_connection"
        case eddingtonData = "eddington_data"
        case recentActivities = "recent_activities"
    }
}

struct SeasonStats: Identifiable, Codable {
    var id: String { seasonName }
    let seasonName: String
    let brevetsCompleted: Int
    let totalDistance: Double
    let rank: Int?
    let isSuperRandonneur: Bool

    enum CodingKeys: String, CodingKey {
        case seasonName = "season_name"
        case brevetsCompleted = "brevets_completed"
        case totalDistance = "total_distance"
        case rank
        case isSuperRandonneur = "is_super_randonneur"
    }
}
