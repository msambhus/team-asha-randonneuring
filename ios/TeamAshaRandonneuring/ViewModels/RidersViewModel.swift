import Foundation

@MainActor
class RidersViewModel: ObservableObject {
    @Published var leaderboard: SeasonLeaderboard?
    @Published var seasons: [Season] = []
    @Published var selectedSeason: Season?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiClient = APIClient.shared

    func loadSeasons() async {
        do {
            seasons = try await apiClient.fetchSeasons()
            if let current = seasons.first(where: { $0.isCurrent == true }) {
                selectedSeason = current
            } else if let first = seasons.first {
                selectedSeason = first
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadLeaderboard(seasonId: Int? = nil) async {
        isLoading = true
        errorMessage = nil

        do {
            leaderboard = try await apiClient.fetchSeasonLeaderboard(seasonId: seasonId)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func selectSeason(_ season: Season) async {
        selectedSeason = season
        await loadLeaderboard(seasonId: season.id)
    }

    func refresh() async {
        await loadSeasons()
        if let seasonId = selectedSeason?.id {
            await loadLeaderboard(seasonId: seasonId)
        } else {
            await loadLeaderboard()
        }
    }
}
