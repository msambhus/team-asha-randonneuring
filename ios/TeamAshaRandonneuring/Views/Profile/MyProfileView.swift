import SwiftUI

struct MyProfileView: View {
    @EnvironmentObject var authService: AuthService
    @StateObject private var viewModel = ProfileViewModel()
    @State private var showingSettings = false

    var body: some View {
        NavigationView {
            ScrollView {
                if viewModel.isLoading {
                    ProgressView("Loading profile...")
                        .padding()
                } else if let error = viewModel.errorMessage {
                    ErrorView(message: error) {
                        Task {
                            await viewModel.loadProfile()
                        }
                    }
                } else if let profile = viewModel.profile {
                    profileContent(profile)
                }
            }
            .navigationTitle("My Profile")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: { showingSettings = true }) {
                        Image(systemName: "gearshape")
                    }
                }
            }
            .task {
                await viewModel.loadProfile()
            }
            .refreshable {
                await viewModel.refresh()
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView()
            }
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

                if let email = profile.rider.email {
                    Text(email)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.top)

            // Career Stats
            if let total = profile.rider.totalBrevets, let distance = profile.rider.totalDistance {
                StatsGrid(
                    brevets: total,
                    distance: distance,
                    isSR: profile.rider.isSuperRandonneur == true,
                    isPBP: profile.rider.pbpFinisher == true
                )
            }

            // Strava Connection
            StravaSection(
                connection: profile.stravaConnection,
                eddingtonData: profile.eddingtonData,
                onSync: {
                    Task {
                        await viewModel.syncStrava()
                    }
                },
                isSyncing: viewModel.isSyncing,
                syncMessage: viewModel.syncMessage
            )

            // Recent Activities
            if let activities = profile.recentActivities, !activities.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Recent Activities")
                        .font(.headline)
                        .padding(.horizontal)

                    ForEach(activities.prefix(10)) { activity in
                        ActivityRow(activity: activity)
                    }
                }
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
        }
        .padding()
    }
}

struct StatsGrid: View {
    let brevets: Int
    let distance: Double
    let isSR: Bool
    let isPBP: Bool

    var body: some View {
        VStack(spacing: 16) {
            Text("Career Stats")
                .font(.headline)

            HStack(spacing: 40) {
                StatCard(title: "Brevets", value: "\(brevets)")
                StatCard(title: "Distance", value: String(format: "%.0fkm", distance))
            }

            HStack(spacing: 12) {
                if isSR {
                    Badge(text: "Super Randonneur", color: .green)
                }
                if isPBP {
                    Badge(text: "PBP Finisher", color: .purple)
                }
            }
        }
        .padding()
        .background(Color(.systemGray6))
        .cornerRadius(12)
        .padding(.horizontal)
    }
}

struct SettingsView: View {
    @EnvironmentObject var authService: AuthService
    @Environment(\.dismiss) var dismiss

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("Account")) {
                    if let user = authService.currentUser {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(user.name)
                                .font(.headline)
                            if let email = user.email {
                                Text(email)
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                }

                Section(header: Text("App Info")) {
                    HStack {
                        Text("Version")
                        Spacer()
                        Text("1.0.0")
                            .foregroundColor(.secondary)
                    }
                }

                Section {
                    Button(action: {
                        authService.signOut()
                        dismiss()
                    }) {
                        Text("Sign Out")
                            .foregroundColor(.red)
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarItems(trailing: Button("Done") {
                dismiss()
            })
        }
    }
}

#Preview {
    MyProfileView()
        .environmentObject(AuthService.shared)
        .environmentObject(APIClient.shared)
}
