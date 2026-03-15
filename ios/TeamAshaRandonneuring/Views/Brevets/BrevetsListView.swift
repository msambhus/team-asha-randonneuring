import SwiftUI

struct BrevetsListView: View {
    @StateObject private var viewModel = BrevetsViewModel()

    var body: some View {
        NavigationView {
            Group {
                if viewModel.isLoading && viewModel.brevets.isEmpty {
                    ProgressView("Loading brevets...")
                } else if let error = viewModel.errorMessage {
                    ErrorView(message: error) {
                        Task {
                            await viewModel.refresh()
                        }
                    }
                } else if viewModel.brevets.isEmpty {
                    EmptyStateView(
                        icon: "calendar.badge.exclamationmark",
                        title: "No Upcoming Brevets",
                        message: "Check back later for upcoming events"
                    )
                } else {
                    brevetsList
                }
            }
            .navigationTitle("Upcoming Brevets")
            .task {
                await viewModel.loadBrevets()
            }
            .refreshable {
                await viewModel.refresh()
            }
        }
    }

    private var brevetsList: some View {
        List {
            ForEach(groupedBrevets.keys.sorted(), id: \.self) { month in
                Section(header: Text(month)) {
                    ForEach(groupedBrevets[month] ?? []) { brevet in
                        NavigationLink(destination: BrevetDetailView(brevet: brevet)) {
                            BrevetRow(brevet: brevet)
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
    }

    private var groupedBrevets: [String: [Brevet]] {
        Dictionary(grouping: viewModel.brevets) { brevet in
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            guard let date = formatter.date(from: brevet.date) else {
                return "Unknown"
            }
            formatter.dateFormat = "MMMM yyyy"
            return formatter.string(from: date)
        }
    }
}

struct BrevetRow: View {
    let brevet: Brevet

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(brevet.name)
                    .font(.headline)
                Spacer()
                DistanceBadge(distance: brevet.distance)
            }

            HStack(spacing: 12) {
                Label(brevet.formattedDate, systemImage: "calendar")
                    .font(.subheadline)
                    .foregroundColor(.secondary)

                if let location = brevet.location {
                    Label(location, systemImage: "mappin.circle")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
            }

            if let elevation = brevet.elevationDisplay {
                Label(elevation, systemImage: "mountain.2")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

struct DistanceBadge: View {
    let distance: Int

    var color: Color {
        switch distance {
        case 0..<200: return .green
        case 200..<400: return .blue
        case 400..<600: return .orange
        case 600...: return .red
        default: return .gray
        }
    }

    var body: some View {
        Text("\(distance)km")
            .font(.system(size: 14, weight: .bold))
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(color.opacity(0.2))
            .foregroundColor(color)
            .cornerRadius(8)
    }
}

#Preview {
    BrevetsListView()
        .environmentObject(APIClient.shared)
}
