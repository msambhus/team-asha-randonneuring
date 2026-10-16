import SwiftUI

struct RidersListView: View {
    @StateObject private var viewModel = RidersViewModel()

    var body: some View {
        NavigationView {
            Group {
                if viewModel.isLoading && viewModel.leaderboard == nil {
                    ProgressView("Loading riders...")
                } else if let error = viewModel.errorMessage {
                    ErrorView(message: error) {
                        Task {
                            await viewModel.refresh()
                        }
                    }
                } else if let leaderboard = viewModel.leaderboard {
                    leaderboardView(leaderboard)
                } else {
                    EmptyStateView(
                        icon: "person.3",
                        title: "No Riders",
                        message: "No riders found for this season"
                    )
                }
            }
            .navigationTitle("Season Leaderboard")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    seasonPicker
                }
            }
            .task {
                await viewModel.loadSeasons()
                await viewModel.loadLeaderboard()
            }
            .refreshable {
                await viewModel.refresh()
            }
        }
    }

    private func leaderboardView(_ leaderboard: SeasonLeaderboard) -> some View {
        List {
            Section(header: seasonHeader(leaderboard.season)) {
                ForEach(leaderboard.riders) { entry in
                    NavigationLink(destination: RiderDetailView(riderId: entry.riderId)) {
                        RiderLeaderboardRow(entry: entry)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
    }

    private func seasonHeader(_ season: Season) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(season.name)
                .font(.headline)
            if let startDate = season.startDate, let endDate = season.endDate {
                Text("\(startDate) - \(endDate)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private var seasonPicker: some View {
        Menu {
            ForEach(viewModel.seasons) { season in
                Button(action: {
                    Task {
                        await viewModel.selectSeason(season)
                    }
                }) {
                    HStack {
                        Text(season.name)
                        if season.id == viewModel.selectedSeason?.id {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
        } label: {
            HStack {
                Text(viewModel.selectedSeason?.name ?? "Season")
                    .font(.subheadline)
                Image(systemName: "chevron.down")
                    .font(.caption)
            }
        }
    }
}

struct RiderLeaderboardRow: View {
    let entry: LeaderboardEntry

    var body: some View {
        HStack(spacing: 12) {
            // Rank
            Text("#\(entry.rank)")
                .font(.system(size: 18, weight: .bold))
                .foregroundColor(.blue)
                .frame(width: 40, alignment: .leading)

            // Photo
            if let photoUrl = entry.photoUrl {
                AsyncImage(url: URL(string: photoUrl)) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } placeholder: {
                    Image(systemName: "person.circle.fill")
                        .resizable()
                }
                .frame(width: 40, height: 40)
                .clipShape(Circle())
            } else {
                Image(systemName: "person.circle.fill")
                    .resizable()
                    .frame(width: 40, height: 40)
                    .foregroundColor(.gray)
            }

            // Name and stats
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(entry.name)
                        .font(.headline)
                    if entry.isSuperRandonneur {
                        Image(systemName: "star.fill")
                            .font(.caption)
                            .foregroundColor(.yellow)
                    }
                }

                HStack(spacing: 16) {
                    Label("\(entry.brevetsCompleted)", systemImage: "checkmark.circle")
                    Label(String(format: "%.0fkm", entry.totalDistance), systemImage: "road.lanes")
                }
                .font(.caption)
                .foregroundColor(.secondary)
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }
}

#Preview {
    RidersListView()
        .environmentObject(APIClient.shared)
}
