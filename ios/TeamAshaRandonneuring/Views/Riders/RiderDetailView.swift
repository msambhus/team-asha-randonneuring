import SwiftUI

struct RiderDetailView: View {
    let riderId: Int
    @StateObject private var viewModel = RiderDetailViewModel()

    var body: some View {
        ScrollView {
            if viewModel.isLoading {
                ProgressView("Loading profile...")
                    .padding()
            } else if let error = viewModel.errorMessage {
                ErrorView(message: error) {
                    Task {
                        await viewModel.loadProfile(riderId: riderId)
                    }
                }
            } else if let profile = viewModel.profile {
                profileContent(profile)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadProfile(riderId: riderId)
        }
        .refreshable {
            await viewModel.loadProfile(riderId: riderId)
        }
    }

    private func profileContent(_ profile: RiderProfile) -> some View {
        VStack(spacing: 24) {
            // Header
            VStack(spacing: 12) {
                if let photoUrl = profile.rider.photoUrl {
                    AsyncImage(url: URL(string: photoUrl)) { image in
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                    } placeholder: {
                        Image(systemName: "person.circle.fill")
                            .resizable()
                    }
                    .frame(width: 100, height: 100)
                    .clipShape(Circle())
                } else {
                    Image(systemName: "person.circle.fill")
                        .resizable()
                        .frame(width: 100, height: 100)
                        .foregroundColor(.gray)
                }

                Text(profile.rider.name)
                    .font(.title)
                    .fontWeight(.bold)
            }
            .padding(.top)

            // Career Stats
            if let total = profile.rider.totalBrevets, let distance = profile.rider.totalDistance {
                VStack(spacing: 16) {
                    Text("Career Stats")
                        .font(.headline)

                    HStack(spacing: 40) {
                        StatCard(title: "Brevets", value: "\(total)")
                        StatCard(title: "Distance", value: String(format: "%.0fkm", distance))
                    }

                    HStack(spacing: 12) {
                        if profile.rider.isSuperRandonneur == true {
                            Badge(text: "Super Randonneur", color: .green)
                        }
                        if profile.rider.pbpFinisher == true {
                            Badge(text: "PBP Finisher", color: .purple)
                        }
                    }
                }
                .padding()
                .background(Color(.systemGray6))
                .cornerRadius(12)
            }

            // Season History
            if !profile.seasonStats.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Season History")
                        .font(.headline)
                        .padding(.horizontal)

                    ForEach(profile.seasonStats) { stats in
                        SeasonStatsRow(stats: stats)
                    }
                }
            }

            // Eddington Number
            if let eddington = profile.eddingtonData {
                EddingtonCard(data: eddington)
                    .padding(.horizontal)
            }

            // Recent Activities
            if let activities = profile.recentActivities, !activities.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Recent Activities")
                        .font(.headline)
                        .padding(.horizontal)

                    ForEach(activities.prefix(5)) { activity in
                        ActivityRow(activity: activity)
                    }
                }
            }
        }
        .padding()
    }
}

struct StatCard: View {
    let title: String
    let value: String

    var body: some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.system(size: 32, weight: .bold))
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }
}

struct Badge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.caption)
            .fontWeight(.semibold)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(color.opacity(0.2))
            .foregroundColor(color)
            .cornerRadius(8)
    }
}

struct SeasonStatsRow: View {
    let stats: SeasonStats

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(stats.seasonName)
                    .font(.headline)
                HStack(spacing: 16) {
                    Text("\(stats.brevetsCompleted) brevets")
                    Text(String(format: "%.0fkm", stats.totalDistance))
                }
                .font(.caption)
                .foregroundColor(.secondary)
            }

            Spacer()

            if let rank = stats.rank {
                Text("#\(rank)")
                    .font(.title3)
                    .fontWeight(.bold)
                    .foregroundColor(.blue)
            }

            if stats.isSuperRandonneur {
                Image(systemName: "star.fill")
                    .foregroundColor(.yellow)
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
        .padding(.horizontal)
    }
}

@MainActor
class RiderDetailViewModel: ObservableObject {
    @Published var profile: RiderProfile?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiClient = APIClient.shared

    func loadProfile(riderId: Int) async {
        isLoading = true
        errorMessage = nil

        do {
            profile = try await apiClient.fetchRiderProfile(riderId: riderId)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }
}

#Preview {
    NavigationView {
        RiderDetailView(riderId: 1)
            .environmentObject(APIClient.shared)
    }
}
