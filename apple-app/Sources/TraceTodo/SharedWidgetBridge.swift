import Foundation

/// Shared configuration and offline snapshot bridge between the main TraceTodo app
/// and the iOS Widget Extension via App Groups (with graceful fallback when unsigned/sideloaded).
enum SharedWidgetBridge {
    static let prodCloudServerURL = "https://todo.531party.cn"
    static let prodCloudDirectIPURL = "http://43.228.79.133:18090"
    static let testCloudServerURL = "http://43.228.79.133:18091"

    #if TRACETODO_TEST_BUILD
    static let isTestBuild = true
    static let appGroupId = "group.com.tracememory.tracetodo.test"
    static let cloudServerURL = testCloudServerURL
    static let localServerURL = "http://127.0.0.1:18091"
    #else
    static let isTestBuild = false
    static let appGroupId = "group.com.tracememory.tracetodo"
    static let cloudServerURL = prodCloudServerURL
    static let localServerURL = "http://127.0.0.1:18090"
    #endif

    private static let kServerURL = "tm_shared_server_url"
    private static let kSpaceId = "tm_shared_space_id"
    private static let kAuthToken = "tm_shared_auth_token"
    private static let kMatrixSnapshot = "tm_shared_matrix_snapshot_json"
    private static let kCountdownsSnapshot = "tm_shared_countdowns_snapshot_json"
    private static let kUpdatedAt = "tm_shared_snapshot_updated_at"

    private static var sharedDefaults: UserDefaults? {
        UserDefaults(suiteName: appGroupId)
    }

    static func saveConfig(serverURL: String, spaceId: String, authToken: String) {
        guard let ud = sharedDefaults else { return }
        let cleanURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanSpace = spaceId.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanToken = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        ud.set(cleanURL.isEmpty ? cloudServerURL : cleanURL, forKey: kServerURL)
        ud.set(cleanSpace.isEmpty ? "default" : cleanSpace, forKey: kSpaceId)
        ud.set(cleanToken, forKey: kAuthToken)
    }

    static func loadConfig() -> (serverURL: String, spaceId: String, authToken: String) {
        let ud = sharedDefaults
        let url = (ud?.string(forKey: kServerURL) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let space = (ud?.string(forKey: kSpaceId) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let token = (ud?.string(forKey: kAuthToken) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return (
            serverURL: url.isEmpty ? cloudServerURL : url,
            spaceId: space.isEmpty ? "default" : space,
            authToken: token
        )
    }

    static func saveMatrixSnapshotData(_ data: Data) {
        guard let ud = sharedDefaults else { return }
        ud.set(data, forKey: kMatrixSnapshot)
        ud.set(Date().timeIntervalSince1970, forKey: kUpdatedAt)
    }

    static func loadMatrixSnapshotData() -> Data? {
        sharedDefaults?.data(forKey: kMatrixSnapshot)
    }

    static func saveCountdownsSnapshotData(_ data: Data) {
        guard let ud = sharedDefaults else { return }
        ud.set(data, forKey: kCountdownsSnapshot)
        ud.set(Date().timeIntervalSince1970, forKey: kUpdatedAt)
    }

    static func loadCountdownsSnapshotData() -> Data? {
        sharedDefaults?.data(forKey: kCountdownsSnapshot)
    }
}
