import SwiftUI

struct ContentView: View {
    @EnvironmentObject var authService: AuthService

    var body: some View {
        Group {
            if authService.isAuthenticated {
                MainTabView()
            } else {
                WelcomeView()
            }
        }
    }
}

struct MainTabView: View {
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            BrevetsListView()
                .tabItem {
                    Label("Brevets", systemImage: "calendar")
                }
                .tag(0)

            RidersListView()
                .tabItem {
                    Label("Riders", systemImage: "person.3.fill")
                }
                .tag(1)

            MyProfileView()
                .tabItem {
                    Label("Profile", systemImage: "person.circle.fill")
                }
                .tag(2)
        }
        .accentColor(.blue)
    }
}

#Preview {
    ContentView()
        .environmentObject(AuthService.shared)
        .environmentObject(APIClient.shared)
}
