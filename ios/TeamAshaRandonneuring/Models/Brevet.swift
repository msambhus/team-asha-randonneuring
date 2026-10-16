import Foundation

struct Brevet: Identifiable, Codable, Hashable {
    let id: Int
    let name: String
    let distance: Int
    let date: String
    let startTime: String?
    let location: String?
    let region: String?
    let rwgpsUrl: String?
    let rusaUrl: String?
    let elevation: Int?
    let seasonId: Int?

    var distanceDisplay: String {
        "\(distance)km"
    }

    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: date) else { return date }

        formatter.dateStyle = .medium
        return formatter.string(from: date)
    }

    var elevationDisplay: String? {
        guard let elevation = elevation else { return nil }
        return "\(elevation)m"
    }

    enum CodingKeys: String, CodingKey {
        case id, name, distance, date, location, region, elevation
        case startTime = "start_time"
        case rwgpsUrl = "rwgps_url"
        case rusaUrl = "rusa_url"
        case seasonId = "season_id"
    }
}

struct BrevetResult: Identifiable, Codable {
    let id: Int
    let riderId: Int
    let brevetId: Int
    let finishTime: String?
    let dnf: Bool
    let dns: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case riderId = "rider_id"
        case brevetId = "brevet_id"
        case finishTime = "finish_time"
        case dnf, dns
    }
}

struct UpcomingBrevetResponse: Codable {
    let brevets: [Brevet]
}
