import SwiftUI

struct EddingtonCard: View {
    let data: EddingtonData

    var badgeColor: Color {
        switch data.badge.level.lowercased() {
        case "legendary": return .yellow
        case "exceptional": return .gray
        case "strong": return Color(red: 0.8, green: 0.5, blue: 0.2) // Bronze
        case "solid": return .blue
        case "building": return .gray
        default: return .gray
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            // Header
            HStack {
                Text("\(data.badge.emoji) E\(data.miles)")
                    .font(.system(size: 32, weight: .bold))
                    .foregroundColor(badgeColor)

                Spacer()

                VStack(alignment: .trailing, spacing: 2) {
                    Text(data.badge.level)
                        .font(.headline)
                        .foregroundColor(badgeColor)
                    Text("E\(data.km) km")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            // Description
            Text("You've ridden \(data.miles)+ miles on \(data.miles)+ different days")
                .font(.subheadline)
                .foregroundColor(.secondary)

            // Progress to next milestone
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Next Milestone: E\(data.progress.next)")
                        .font(.caption)
                        .fontWeight(.semibold)
                    Spacer()
                    Text("\(Int(data.progress.percentage))%")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                // Progress bar
                ProgressView(value: data.progress.percentage / 100.0)
                    .tint(badgeColor)

                Text("\(data.progress.daysCompleted)/\(data.progress.daysNeeded) days completed · \(data.progress.daysNeeded - data.progress.daysCompleted) more needed")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding()
        .background(badgeColor.opacity(0.1))
        .cornerRadius(12)
    }
}

#Preview {
    EddingtonCard(data: EddingtonData(
        miles: 52,
        km: 83,
        progress: EddingtonProgress(
            current: 52,
            next: 53,
            daysCompleted: 49,
            daysNeeded: 53,
            percentage: 92.5
        ),
        badge: EddingtonBadge(
            level: "Strong",
            color: "#CD7F32",
            emoji: "💪"
        )
    ))
    .padding()
}
