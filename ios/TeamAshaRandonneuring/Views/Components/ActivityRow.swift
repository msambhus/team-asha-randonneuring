import SwiftUI

struct ActivityRow: View {
    let activity: StravaActivity

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(activity.name)
                    .font(.headline)
                Spacer()
                if activity.activityType == "Ride" {
                    Image(systemName: "bicycle")
                        .foregroundColor(.orange)
                }
            }

            HStack(spacing: 16) {
                // Distance
                Label(String(format: "%.1fkm", activity.distanceInKm), systemImage: "road.lanes")

                // Duration
                if !activity.formattedDuration.isEmpty {
                    Label(activity.formattedDuration, systemImage: "clock")
                }

                // Elevation
                if let elevation = activity.totalElevationGain {
                    Label(String(format: "%.0fm", elevation), systemImage: "mountain.2")
                }
            }
            .font(.caption)
            .foregroundColor(.secondary)

            // Date
            if let dateLocal = activity.startDateLocal {
                Text(formatDate(dateLocal))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
        .padding(.horizontal)
    }

    private func formatDate(_ dateString: String) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        if let date = formatter.date(from: dateString) {
            formatter.dateStyle = .medium
            formatter.timeStyle = .short
            return formatter.string(from: date)
        }
        // Try alternate format
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        if let date = formatter.date(from: dateString) {
            formatter.dateStyle = .medium
            formatter.timeStyle = .short
            return formatter.string(from: date)
        }
        return dateString
    }
}

#Preview {
    ActivityRow(activity: StravaActivity(
        id: 1,
        stravaActivityId: "12345",
        name: "Morning Ride",
        activityType: "Ride",
        distance: 50000,
        movingTime: 7200,
        elapsedTime: 7500,
        totalElevationGain: 500,
        startDate: "2026-02-28T08:00:00Z",
        startDateLocal: "2026-02-28T08:00:00",
        averageHeartrate: 145,
        maxHeartrate: 175,
        averageWatts: 200,
        stravaUrl: "https://strava.com/activities/12345"
    ))
}
