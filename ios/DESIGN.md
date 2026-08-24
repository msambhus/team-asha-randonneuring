# iOS App Design Document

## Overview

Native iOS app for Team Asha Randonneuring built with SwiftUI, following modern iOS development best practices.

## Design Philosophy

### Native First
- Pure SwiftUI (no UIKit legacy code)
- Native iOS design patterns and components
- SF Symbols for icons
- System fonts and colors
- Automatic dark mode support

### Simplicity
- No external dependencies
- Native URLSession for networking
- async/await for concurrency
- UserDefaults for simple persistence
- Minimal complexity, maximum maintainability

### User Experience
- Fast app launch
- Smooth scrolling
- Pull-to-refresh everywhere
- Proper loading states
- Clear error messages
- Empty state guidance

## Architecture

### MVVM Pattern

```
View ←→ ViewModel ←→ Service ←→ API
  ↓         ↓          ↓
State   @Published  Models
```

**Why MVVM?**
- Clear separation of concerns
- Easy to test business logic
- Works naturally with SwiftUI's @Published
- ViewModels survive view recreation
- Shared state via @EnvironmentObject

### Data Flow

1. **View** displays UI and handles user interaction
2. **ViewModel** manages state and business logic
3. **Service** handles API calls and data transformation
4. **Models** represent data structures

Example:
```swift
// User taps "Load Brevets"
BrevetsListView → BrevetsViewModel.loadBrevets()
  → APIClient.fetchUpcomingBrevets()
  → URLSession request
  → JSON decode to [Brevet]
  → ViewModel.brevets = result
  → View updates automatically
```

## Key Components

### 1. Services Layer

#### APIClient
- Singleton service for all HTTP requests
- Generic request method with type safety
- Configurable base URL (dev/prod)
- Automatic JSON encoding/decoding
- Proper error handling

**Design choices:**
- async/await (no completion handlers)
- Throws errors (no Result type)
- Generic `request<T: Decodable>` method
- Bearer token authentication

#### AuthService
- Singleton for authentication state
- Manages current user and auth token
- Persists to UserDefaults
- ObservableObject for reactive updates

**Design choices:**
- @Published properties for state changes
- UserDefaults for simple persistence (no Keychain yet)
- Demo login for development
- Placeholder for Google Sign-In

### 2. Models Layer

All models conform to:
- `Codable` for JSON encoding/decoding
- `Identifiable` for SwiftUI lists
- `Hashable` (where needed) for Set operations

**Naming convention:**
- `snake_case` in JSON (backend format)
- `camelCase` in Swift (iOS convention)
- CodingKeys enum bridges the gap

Example:
```swift
struct Rider: Identifiable, Codable {
    let id: Int
    let photoUrl: String?  // camelCase in Swift

    enum CodingKeys: String, CodingKey {
        case id
        case photoUrl = "photo_url"  // snake_case from API
    }
}
```

### 3. ViewModels Layer

Each major screen has a ViewModel:
- Marked `@MainActor` for UI updates
- Uses `@Published` for observable state
- Async functions for data loading
- Simple error handling with optional String

**Standard pattern:**
```swift
@MainActor
class BrevetsViewModel: ObservableObject {
    @Published var brevets: [Brevet] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    func loadBrevets() async {
        isLoading = true
        do {
            brevets = try await apiClient.fetchUpcomingBrevets()
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
```

### 4. Views Layer

#### View Hierarchy

```
ContentView (root)
├── WelcomeView (if not authenticated)
└── MainTabView (if authenticated)
    ├── BrevetsListView
    │   └── BrevetDetailView
    ├── RidersListView
    │   └── RiderDetailView
    └── MyProfileView
        └── SettingsView
```

#### Component Library

Reusable components in `Views/Components/`:
- **EddingtonCard**: Eddington number display with progress
- **ActivityRow**: Strava activity list item
- **ErrorView**: Error state with retry
- **EmptyStateView**: No data placeholder
- **DistanceBadge**: Color-coded distance tags
- **StatCard**: Stat display (e.g., "52 Brevets")

## Design Patterns

### 1. Loading States

Every data-loading view follows this pattern:

```swift
if viewModel.isLoading && data.isEmpty {
    ProgressView("Loading...")
} else if let error = viewModel.errorMessage {
    ErrorView(message: error) { /* retry */ }
} else if data.isEmpty {
    EmptyStateView(...)
} else {
    // Display data
}
```

### 2. Pull-to-Refresh

All lists support pull-to-refresh:

```swift
.refreshable {
    await viewModel.refresh()
}
```

### 3. Task Loading

Use `.task` for initial load:

```swift
.task {
    await viewModel.loadBrevets()
}
```

### 4. Navigation

SwiftUI NavigationView with NavigationLink:

```swift
NavigationLink(destination: DetailView(item: item)) {
    ListRow(item: item)
}
```

## Styling Decisions

### Colors

- **Primary**: Blue (system blue)
- **Accents**:
  - Orange for Strava
  - Green for Super Randonneur
  - Purple for PBP Finisher
- **Distance badges**:
  - Green: < 200km
  - Blue: 200-399km
  - Orange: 400-599km
  - Red: 600km+
- **Eddington badges**:
  - Yellow: Legendary (100+)
  - Gray: Exceptional (75-99)
  - Bronze: Strong (50-74)
  - Blue: Solid (25-49)

### Typography

- **Title**: System large title, bold
- **Headline**: System headline
- **Body**: System body
- **Caption**: System caption, secondary color

### Spacing

- **Card padding**: 16pt
- **List spacing**: 12pt
- **Section spacing**: 24pt
- **Corner radius**: 12pt

### Icons

All icons use SF Symbols:
- `calendar` - Dates/brevets
- `bicycle.circle.fill` - App icon
- `person.3.fill` - Riders
- `figure.outdoor.cycle` - Strava
- `mappin.circle` - Location
- `mountain.2` - Elevation
- `road.lanes` - Distance
- `star.fill` - Super Randonneur
- `checkmark.circle.fill` - Status

## API Integration

### Base URL

Production: `https://team-asha-randonneuring.vercel.app`

Configurable for development:
```swift
apiClient.configure(baseURL: "http://localhost:5001")
```

### Request Flow

1. **Build URL** from base + endpoint
2. **Create URLRequest** with method, headers, body
3. **Add auth** if authenticated endpoint
4. **Execute** with URLSession.data(for:)
5. **Validate** HTTP status code
6. **Decode** JSON to model type
7. **Return** or throw error

### Error Handling

Custom `APIError` enum:
- `invalidURL` - Malformed URL
- `invalidResponse` - Not HTTP response
- `httpError(Int)` - Non-2xx status code
- `decodingError(Error)` - JSON parsing failed
- `unauthorized` - 401 response

All errors conform to `LocalizedError` for user-friendly messages.

## Authentication Flow

### Current: Demo Login

```
WelcomeView
  → Demo Login Button
  → Enter email
  → AuthService.demoLogin()
  → Create demo user + token
  → Save to UserDefaults
  → isAuthenticated = true
  → ContentView shows MainTabView
```

### Future: Google Sign-In

```
WelcomeView
  → Google Sign-In Button
  → GoogleSignIn SDK
  → Get ID token
  → POST /auth/google/callback with token
  → Receive session token + user data
  → Save to UserDefaults
  → isAuthenticated = true
```

## State Management

### Global State

**AuthService** (shared singleton):
- `@Published var isAuthenticated: Bool`
- `@Published var currentUser: Rider?`
- `@Published var authToken: String?`

Injected via `@EnvironmentObject` in App.swift

**APIClient** (shared singleton):
- Stateless service
- Reads auth token from AuthService
- Configurable base URL

### View-Specific State

Each ViewModel owns its screen's state:
- List data arrays
- Loading flags
- Error messages
- Filter/sort preferences

### Persistence

**UserDefaults** for:
- Auth token (key: "auth_token")
- Current user JSON (key: "current_user")

**Future**: Migrate sensitive data to Keychain

## Performance Considerations

### Lazy Loading

Lists use LazyVStack/LazyHStack for performance:
- Only renders visible rows
- Scrolling stays smooth with 100+ items

### Image Caching

AsyncImage with automatic caching:
```swift
AsyncImage(url: URL(string: photoUrl)) { image in
    image.resizable()
} placeholder: {
    ProgressView()
}
```

### API Efficiency

- Pull-to-refresh doesn't re-fetch if data fresh
- Strava sync is manual (not automatic)
- Leaderboard loads per season (not all at once)

## Accessibility

### VoiceOver Support

- All images have labels
- Buttons have clear titles
- Lists announce count

### Dynamic Type

- Uses system fonts (auto-scales)
- Layout adapts to text size

### Color Contrast

- Meets WCAG AA standards
- Works in light and dark mode

## iPad Support

All layouts use:
- `.frame(maxWidth: .infinity)` for flexibility
- `.padding()` for gutters
- List style: `.insetGrouped` (adapts to iPad)
- Tab bar works on iPad

No special iPad layouts needed - SwiftUI handles it.

## Testing Strategy

### Manual Testing

- Run on simulator (iPhone 15 Pro, iPad Pro)
- Test in light and dark mode
- Test with different text sizes
- Test with slow network (Network Link Conditioner)
- Test error states (mock server down)

### Future: Unit Tests

Test ViewModels:
```swift
func testLoadBrevets() async {
    let vm = BrevetsViewModel()
    await vm.loadBrevets()
    XCTAssertFalse(vm.brevets.isEmpty)
    XCTAssertFalse(vm.isLoading)
}
```

### Future: UI Tests

Test navigation flows:
```swift
func testBrevetDetailNavigation() {
    let app = XCUIApplication()
    app.launch()
    // Tap first brevet
    // Verify detail screen
}
```

## Security

### Current

- HTTPS only (NSAppTransportSecurity)
- No arbitrary loads allowed
- Auth token in memory + UserDefaults

### Future Improvements

- Store auth token in Keychain
- Certificate pinning
- Biometric auth option
- Auto-logout after inactivity

## Deployment

### App Store Checklist

1. **Assets**
   - App icon (all sizes)
   - Launch screen
   - Screenshots (iPhone, iPad)

2. **Metadata**
   - App name: "Team Asha Randonneuring"
   - Subtitle: "Ultra-Distance Cycling"
   - Description
   - Keywords
   - Support URL

3. **Build Settings**
   - Bundle ID: com.teamasha.randonneuring
   - Version: 1.0
   - Build: 1
   - Deployment target: iOS 16.0

4. **Certificates**
   - Development certificate
   - Distribution certificate
   - Provisioning profiles

5. **Privacy**
   - Privacy policy URL
   - Data collection disclosure

### TestFlight

Before App Store:
1. Upload build to App Store Connect
2. Add to TestFlight
3. Invite internal testers
4. Gather feedback
5. Fix bugs
6. Submit for review

## Future Roadmap

### Phase 1: Core Features (Complete ✅)
- Brevets calendar
- Rider leaderboard
- User profile
- Strava integration
- Eddington Number

### Phase 2: Authentication
- Real Google Sign-In
- Strava OAuth
- Session management
- Secure token storage

### Phase 3: Enhanced Features
- Push notifications
- Offline mode
- Search and filters
- Brevet sign-up flow
- Training plans

### Phase 4: Advanced
- Apple Watch app
- Widgets
- Live Activities
- Route navigation
- Social features

## Maintenance

### Adding New Features

1. **Model**: Add/update models in `Models/`
2. **API**: Add endpoint in `APIClient`
3. **ViewModel**: Create/update ViewModel
4. **View**: Build UI in `Views/`
5. **Test**: Manual testing
6. **Document**: Update this file

### Debugging Tips

- Use `print()` for quick debugging
- Use breakpoints in Xcode
- Check console for API errors
- Use Instruments for performance
- Test on real device, not just simulator

### Common Issues

**Build errors**: Clean build folder
**API fails**: Check base URL and network
**State not updating**: Ensure @Published and @MainActor
**Layout issues**: Test on multiple devices

---

**Last Updated**: 2026-02-28
**Version**: 1.0
**Status**: Initial Release
