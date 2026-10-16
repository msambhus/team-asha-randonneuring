import Foundation

@MainActor
class BrevetsViewModel: ObservableObject {
    @Published var brevets: [Brevet] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiClient = APIClient.shared

    func loadBrevets() async {
        isLoading = true
        errorMessage = nil

        do {
            brevets = try await apiClient.fetchUpcomingBrevets()
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func refresh() async {
        await loadBrevets()
    }
}
