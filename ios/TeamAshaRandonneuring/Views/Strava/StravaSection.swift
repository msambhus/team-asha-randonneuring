import SwiftUI

struct StravaSection: View {
    let connection: StravaConnection?
    let eddingtonData: EddingtonData?
    let onSync: () -> Void
    let isSyncing: Bool
    let syncMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            // Header
            HStack {
                Image(systemName: "figure.outdoor.cycle")
                    .font(.title2)
                    .foregroundColor(.orange)
                Text("Strava")
                    .font(.headline)
                Spacer()
                if connection != nil {
                    Button(action: onSync) {
                        if isSyncing {
                            ProgressView()
                        } else {
                            HStack(spacing: 4) {
                                Image(systemName: "arrow.triangle.2.circlepath")
                                Text("Sync")
                            }
                            .font(.caption)
                        }
                    }
                    .disabled(isSyncing)
                }
            }
            .padding(.horizontal)

            if let connection = connection {
                // Connected state
                VStack(spacing: 12) {
                    // Sync status
                    if let lastSync = connection.lastSyncAt {
                        HStack {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundColor(.green)
                            Text("Last synced: \(formatDate(lastSync))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .padding(.horizontal)
                    }

                    // Sync message
                    if let message = syncMessage {
                        HStack {
                            Image(systemName: "info.circle.fill")
                                .foregroundColor(.blue)
                            Text(message)
                                .font(.caption)
                        }
                        .padding(.horizontal)
                    }

                    // Eddington Number
                    if let eddington = eddingtonData {
                        EddingtonCard(data: eddington)
                            .padding(.horizontal)
                    }
                }
            } else {
                // Not connected state
                VStack(spacing: 12) {
                    Text("Connect Strava to track your activities and see your Eddington Number")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)

                    Button(action: {
                        // Open Strava connection flow
                        // This would open a web view or Safari for OAuth
                    }) {
                        HStack {
                            Image(systemName: "link")
                            Text("Connect Strava")
                        }
                        .font(.headline)
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.orange)
                        .cornerRadius(12)
                    }
                    .padding(.horizontal)
                }
            }
        }
        .padding(.vertical)
        .background(Color(.systemGray6))
        .cornerRadius(12)
        .padding(.horizontal)
    }

    private func formatDate(_ dateString: String) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        if let date = formatter.date(from: dateString) {
            formatter.dateStyle = .short
            formatter.timeStyle = .short
            return formatter.string(from: date)
        }
        return dateString
    }
}

#Preview {
    StravaSection(
        connection: StravaConnection(
            riderId: 1,
            stravaAthleteId: "12345",
            connectedAt: "2026-01-01T00:00:00",
            lastSyncAt: "2026-02-28T12:00:00",
            eddingtonNumberMiles: 52,
            eddingtonNumberKm: 83,
            eddingtonCalculatedAt: "2026-02-28T12:00:00"
        ),
        eddingtonData: EddingtonData(
            miles: 52,
            km: 83,
            progress: EddingtonProgress(
                current: 52,
                next: 53,
                daysCompleted: 49,
                daysNeeded: 53,
                percentage: 92.5
            ),
            badge: EddingtonBadge(
                level: "Strong",
                color: "#CD7F32",
                emoji: "💪"
            )
        ),
        onSync: {},
        isSyncing: false,
        syncMessage: nil
    )
}
