import SwiftUI

struct BrevetDetailView: View {
    let brevet: Brevet

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                // Header with distance badge
                VStack(alignment: .leading, spacing: 12) {
                    Text(brevet.name)
                        .font(.title)
                        .fontWeight(.bold)

                    DistanceBadge(distance: brevet.distance)
                }

                Divider()

                // Event Details
                VStack(alignment: .leading, spacing: 16) {
                    DetailRow(icon: "calendar", title: "Date", value: brevet.formattedDate)

                    if let time = brevet.startTime {
                        DetailRow(icon: "clock", title: "Start Time", value: time)
                    }

                    if let location = brevet.location {
                        DetailRow(icon: "mappin.circle", title: "Location", value: location)
                    }

                    if let region = brevet.region {
                        DetailRow(icon: "map", title: "Region", value: region)
                    }

                    if let elevation = brevet.elevationDisplay {
                        DetailRow(icon: "mountain.2", title: "Elevation", value: elevation)
                    }
                }

                Divider()

                // External Links
                VStack(spacing: 12) {
                    if let rwgpsUrl = brevet.rwgpsUrl {
                        LinkButton(title: "View Route on RideWithGPS", url: rwgpsUrl, icon: "map.fill")
                    }

                    if let rusaUrl = brevet.rusaUrl {
                        LinkButton(title: "View on RUSA", url: rusaUrl, icon: "link")
                    }
                }
            }
            .padding()
        }
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct DetailRow: View {
    let icon: String
    let title: String
    let value: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .frame(width: 24)
                .foregroundColor(.blue)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(value)
                    .font(.body)
            }
        }
    }
}

struct LinkButton: View {
    let title: String
    let url: String
    let icon: String

    var body: some View {
        Button(action: {
            if let url = URL(string: url) {
                UIApplication.shared.open(url)
            }
        }) {
            HStack {
                Image(systemName: icon)
                Text(title)
                Spacer()
                Image(systemName: "arrow.up.right")
            }
            .padding()
            .background(Color.blue.opacity(0.1))
            .foregroundColor(.blue)
            .cornerRadius(12)
        }
    }
}

#Preview {
    NavigationView {
        BrevetDetailView(brevet: Brevet(
            id: 1,
            name: "SF Randonneurs 200km",
            distance: 200,
            date: "2026-03-15",
            startTime: "07:00 AM",
            location: "San Francisco",
            region: "SFR",
            rwgpsUrl: "https://ridewithgps.com/routes/12345",
            rusaUrl: "https://rusa.org/event/12345",
            elevation: 2500,
            seasonId: 1
        ))
    }
}
