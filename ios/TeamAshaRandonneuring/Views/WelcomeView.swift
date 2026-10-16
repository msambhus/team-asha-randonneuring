import SwiftUI

struct WelcomeView: View {
    @EnvironmentObject var authService: AuthService
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showDemoLogin = false
    @State private var demoEmail = ""

    var body: some View {
        NavigationView {
            VStack(spacing: 30) {
                Spacer()

                // App Icon / Logo
                Image(systemName: "bicycle.circle.fill")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 120, height: 120)
                    .foregroundColor(.blue)

                VStack(spacing: 12) {
                    Text("Team Asha")
                        .font(.system(size: 36, weight: .bold))
                    Text("Randonneuring")
                        .font(.system(size: 28, weight: .medium))
                        .foregroundColor(.secondary)
                }

                Text("Bay Area cyclists pushing boundaries through ultra-distance randonneuring")
                    .font(.body)
                    .multilineTextAlignment(.center)
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 40)

                Spacer()

                VStack(spacing: 16) {
                    // Google Sign In Button
                    Button(action: {
                        Task {
                            await signInWithGoogle()
                        }
                    }) {
                        HStack {
                            Image(systemName: "g.circle.fill")
                            Text("Sign in with Google")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.white)
                        .foregroundColor(.black)
                        .cornerRadius(12)
                        .shadow(color: .black.opacity(0.1), radius: 4, x: 0, y: 2)
                    }
                    .disabled(isLoading)

                    // Demo Login (for development)
                    Button(action: {
                        showDemoLogin = true
                    }) {
                        Text("Demo Login")
                            .font(.caption)
                            .foregroundColor(.blue)
                    }

                    if let error = errorMessage {
                        Text(error)
                            .font(.caption)
                            .foregroundColor(.red)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }
                }
                .padding(.horizontal, 40)
                .padding(.bottom, 50)
            }
            .navigationBarHidden(true)
        }
        .sheet(isPresented: $showDemoLogin) {
            DemoLoginSheet(isPresented: $showDemoLogin, demoEmail: $demoEmail)
        }
    }

    private func signInWithGoogle() async {
        isLoading = true
        errorMessage = nil

        // Note: Google Sign-In not yet implemented
        // This would require GoogleSignIn SDK integration
        errorMessage = "Google Sign-In requires GoogleSignIn SDK integration. Use Demo Login for now."

        isLoading = false
    }
}

struct DemoLoginSheet: View {
    @EnvironmentObject var authService: AuthService
    @Binding var isPresented: Bool
    @Binding var demoEmail: String
    @State private var isLoading = false

    var body: some View {
        NavigationView {
            Form {
                Section(header: Text("Demo Login")) {
                    TextField("Email", text: $demoEmail)
                        .textContentType(.emailAddress)
                        .autocapitalization(.none)
                        .keyboardType(.emailAddress)
                }

                Section {
                    Button(action: {
                        Task {
                            await performDemoLogin()
                        }
                    }) {
                        if isLoading {
                            ProgressView()
                        } else {
                            Text("Login")
                        }
                    }
                    .disabled(demoEmail.isEmpty || isLoading)
                }

                Section(footer: Text("This is a demo login for development. In production, use Google Sign-In.")) {
                    EmptyView()
                }
            }
            .navigationTitle("Demo Login")
            .navigationBarItems(trailing: Button("Cancel") {
                isPresented = false
            })
        }
    }

    private func performDemoLogin() async {
        isLoading = true
        do {
            try await authService.demoLogin(email: demoEmail)
            isPresented = false
        } catch {
            print("Demo login failed: \(error)")
        }
        isLoading = false
    }
}

#Preview {
    WelcomeView()
        .environmentObject(AuthService.shared)
}
