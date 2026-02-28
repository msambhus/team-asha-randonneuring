import Foundation

struct StravaConnection: Codable {
    let riderId: Int
    let stravaAthleteId: String
    let connectedAt: String?
    let lastSyncAt: String?
    let eddingtonNumberMiles: Int?
    let eddingtonNumberKm: Int?
    let eddingtonCalculatedAt: String?

    enum CodingKeys: String, CodingKey {
        case riderId = "rider_id"
        case stravaAthleteId = "strava_athlete_id"
        case connectedAt = "connected_at"
        case lastSyncAt = "last_sync_at"
        case eddingtonNumberMiles = "eddington_number_miles"
        case eddingtonNumberKm = "eddington_number_km"
        case eddingtonCalculatedAt = "eddington_calculated_at"
    }
}

struct StravaActivity: Identifiable, Codable {
    let id: Int
    let stravaActivityId: String
    let name: String
    let activityType: String
    let distance: Double?
    let movingTime: Int?
    let elapsedTime: Int?
    let totalElevationGain: Double?
    let startDate: String?
    let startDateLocal: String?
    let averageHeartrate: Double?
    let maxHeartrate: Double?
    let averageWatts: Double?
    let stravaUrl: String?

    var distanceInKm: Double {
        (distance ?? 0) / 1000
    }

    var distanceInMiles: Double {
        (distance ?? 0) / 1609.34
    }

    var formattedDuration: String {
        guard let seconds = movingTime else { return "" }
        let hours = seconds / 3600
        let minutes = (seconds % 3600) / 60
        return String(format: "%dh %dm", hours, minutes)
    }

    enum CodingKeys: String, CodingKey {
        case id
        case stravaActivityId = "strava_activity_id"
        case name
        case activityType = "activity_type"
        case distance
        case movingTime = "moving_time"
        case elapsedTime = "elapsed_time"
        case totalElevationGain = "total_elevation_gain"
        case startDate = "start_date"
        case startDateLocal = "start_date_local"
        case averageHeartrate = "average_heartrate"
        case maxHeartrate = "max_heartrate"
        case averageWatts = "average_watts"
        case stravaUrl = "strava_url"
    }
}

struct EddingtonData: Codable {
    let miles: Int
    let km: Int
    let progress: EddingtonProgress
    let badge: EddingtonBadge
}

struct EddingtonProgress: Codable {
    let current: Int
    let next: Int
    let daysCompleted: Int
    let daysNeeded: Int
    let percentage: Double

    enum CodingKeys: String, CodingKey {
        case current, next
        case daysCompleted = "days_completed"
        case daysNeeded = "days_needed"
        case percentage
    }
}

struct EddingtonBadge: Codable {
    let level: String
    let color: String
    let emoji: String
}
