import Foundation

@MainActor
class ProfileViewModel: ObservableObject {
    @Published var profile: RiderProfile?
    @Published var isLoading = false
    @Published var isSyncing = false
    @Published var errorMessage: String?
    @Published var syncMessage: String?

    private let apiClient = APIClient.shared

    func loadProfile() async {
        isLoading = true
        errorMessage = nil

        do {
            profile = try await apiClient.fetchMyProfile()
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func syncStrava() async {
        isSyncing = true
        syncMessage = nil
        errorMessage = nil

        do {
            let response = try await apiClient.syncStravaActivities()
            syncMessage = "Synced \(response.count) activities"
            // Reload profile to get updated Eddington number
            await loadProfile()
        } catch {
            errorMessage = error.localizedDescription
        }

        isSyncing = false
    }

    func refresh() async {
        await loadProfile()
    }
}
