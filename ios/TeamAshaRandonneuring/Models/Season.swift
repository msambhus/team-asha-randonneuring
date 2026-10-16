import Foundation

struct Season: Identifiable, Codable, Hashable {
    let id: Int
    let name: String
    let startDate: String?
    let endDate: String?
    let isCurrent: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name
        case startDate = "start_date"
        case endDate = "end_date"
        case isCurrent = "is_current"
    }
}

struct SeasonLeaderboard: Codable {
    let season: Season
    let riders: [LeaderboardEntry]
}

struct LeaderboardEntry: Identifiable, Codable {
    var id: Int { riderId }
    let riderId: Int
    let name: String
    let photoUrl: String?
    let brevetsCompleted: Int
    let totalDistance: Double
    let rank: Int
    let isSuperRandonneur: Bool

    enum CodingKeys: String, CodingKey {
        case riderId = "rider_id"
        case name
        case photoUrl = "photo_url"
        case brevetsCompleted = "brevets_completed"
        case totalDistance = "total_distance"
        case rank
        case isSuperRandonneur = "is_super_randonneur"
    }
}
