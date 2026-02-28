import SwiftUI

@main
struct TeamAshaRandonneuringApp: App {
    @StateObject private var authService = AuthService.shared
    @StateObject private var apiClient = APIClient.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(authService)
                .environmentObject(apiClient)
                .onAppear {
                    // Configure API client
                    apiClient.configure()
                }
        }
    }
}
