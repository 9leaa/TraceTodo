import Foundation
import Security

/// Multi-channel shared configuration and offline snapshot bridge between the main TraceTodo app
/// and the Widget Extension across:
/// 1. Shared Keychain (`keychain-access-groups` — works even on Free Apple IDs & Sideloadly/AltStore!)
/// 2. App Groups (`UserDefaults(suiteName:)` + Group Container JSON file, including dynamically rewritten sideloader bundle IDs)
/// 3. Shared Application Support JSON file (works on non-sandboxed macOS builds)
enum SharedWidgetBridge {
    static let prodCloudServerURL = "https://todo.531party.cn"
    static let prodCloudDirectIPURL = "http://43.228.79.133:18090"
    static let testCloudServerURL = "http://43.228.79.133:18091"

    #if TRACETODO_TEST_BUILD
    static let isTestBuild = true
    static let appGroupId = "group.com.tracememory.tracetodo.test"
    static let cloudServerURL = testCloudServerURL
    static let localServerURL = "http://127.0.0.1:18091"
    private static let keychainService = "com.tracememory.tracetodo.test.widgetbridge"
    #else
    static let isTestBuild = false
    static let appGroupId = "group.com.tracememory.tracetodo"
    static let cloudServerURL = prodCloudServerURL
    static let localServerURL = "http://127.0.0.1:18090"
    private static let keychainService = "com.tracememory.tracetodo.widgetbridge"
    #endif

    private static let kServerURL = "tm_shared_server_url"
    private static let kSpaceId = "tm_shared_space_id"
    private static let kAuthToken = "tm_shared_auth_token"
    private static let kMatrixSnapshot = "tm_shared_matrix_snapshot_json"
    private static let kCountdownsSnapshot = "tm_shared_countdowns_snapshot_json"
    private static let kUpdatedAt = "tm_shared_snapshot_updated_at"

    private struct BridgeConfigEnvelope: Codable {
        var serverURL: String
        var spaceId: String
        var authToken: String
        var updatedAt: Double
    }

    // MARK: - Candidate App Groups (handles sideloaders that rewrite bundle IDs)

    private static var candidateAppGroups: [String] {
        var list: [String] = [appGroupId, "\(appGroupId).ios"]
        if let bid = Bundle.main.bundleIdentifier, !bid.isEmpty {
            let hostBid: String
            if bid.hasSuffix(".widgets") {
                hostBid = String(bid.dropLast(".widgets".count))
            } else {
                hostBid = bid
            }
            let derived = "group.\(hostBid)"
            if !list.contains(derived) {
                list.insert(derived, at: 0)
            }
        }
        return list
    }

    // MARK: - Shared File Fallback (macOS Application Support & App Group Container)

    private static var candidateSharedFiles: [URL] {
        var urls: [URL] = []
        for grp in candidateAppGroups {
            if let container = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: grp) {
                urls.append(container.appendingPathComponent("widget_bridge_state.json"))
            }
        }
        #if os(macOS)
        if let appSup = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
            let folder = isTestBuild ? "TraceTodo-Test" : "TraceTodo"
            let dir = appSup.appendingPathComponent(folder, isDirectory: true)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            urls.append(dir.appendingPathComponent("widget_bridge_state.json"))
        }
        #endif
        return urls
    }

    // MARK: - Keychain Read / Write (Works across App + Extension on Free & Paid Apple IDs)

    private static let candidateSharedAccessGroups: [String] = {
        let probeAccount = "__tt_prefix_probe"
        let baseQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: probeAccount
        ]
        var fetchQuery = baseQuery
        fetchQuery[kSecReturnAttributes as String] = true
        fetchQuery[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        var status = SecItemCopyMatching(fetchQuery as CFDictionary, &result)
        if status == errSecItemNotFound {
            var add = baseQuery
            add[kSecValueData as String] = Data([1])
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            add[kSecReturnAttributes as String] = true
            status = SecItemAdd(add as CFDictionary, &result)
        }
        guard status == errSecSuccess,
              let dict = result as? [String: Any],
              let ag = dict[kSecAttrAccessGroup as String] as? String,
              let dotIdx = ag.firstIndex(of: ".") else {
            return []
        }
        let prefix = String(ag[..<dotIdx])
        guard !prefix.isEmpty else { return [] }

        var groups: [String] = ["\(prefix).com.tracememory.tracetodo.shared"]
        if let bid = Bundle.main.bundleIdentifier, !bid.isEmpty {
            let hostBid = bid.hasSuffix(".widgets")
                ? String(bid.dropLast(".widgets".count))
                : bid
            let hostGroup = "\(prefix).\(hostBid)"
            if !groups.contains(hostGroup) {
                groups.append(hostGroup)
            }
        }
        return groups
    }()

    private static func keychainSave(account: String, data: Data) {
        var wroteShared = false
        for sharedAG in candidateSharedAccessGroups {
            let agQuery: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: keychainService,
                kSecAttrAccount as String: account,
                kSecAttrAccessGroup as String: sharedAG
            ]
            SecItemDelete(agQuery as CFDictionary)
            var addAG = agQuery
            addAG[kSecValueData as String] = data
            addAG[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            if SecItemAdd(addAG as CFDictionary, nil) == errSecSuccess {
                wroteShared = true
            }
        }
        if !wroteShared {
            let query: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: keychainService,
                kSecAttrAccount as String: account
            ]
            SecItemDelete(query as CFDictionary)
            var addQuery = query
            addQuery[kSecValueData as String] = data
            addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            _ = SecItemAdd(addQuery as CFDictionary, nil)
        }
    }

    private static func keychainLoad(account: String) -> Data? {
        for sharedAG in candidateSharedAccessGroups {
            let agQuery: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: keychainService,
                kSecAttrAccount as String: account,
                kSecAttrAccessGroup as String: sharedAG,
                kSecReturnData as String: true,
                kSecMatchLimit as String: kSecMatchLimitOne
            ]
            var agItem: CFTypeRef?
            if SecItemCopyMatching(agQuery as CFDictionary, &agItem) == errSecSuccess,
               let data = agItem as? Data {
                return data
            }
        }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecSuccess, let data = item as? Data {
            return data
        }
        return nil
    }

    // MARK: - Public API

    static func saveConfig(serverURL: String, spaceId: String, authToken: String) {
        let cleanURL = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanSpace = spaceId.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanToken = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        let effURL = cleanURL.isEmpty ? cloudServerURL : cleanURL
        let effSpace = cleanSpace.isEmpty ? "default" : cleanSpace

        // 1. Keychain
        let env = BridgeConfigEnvelope(
            serverURL: effURL,
            spaceId: effSpace,
            authToken: cleanToken,
            updatedAt: Date().timeIntervalSince1970
        )
        if let encoded = try? JSONEncoder().encode(env) {
            keychainSave(account: "config", data: encoded)
            // 2. Container / AppSupport files
            for fileURL in candidateSharedFiles {
                try? encoded.write(to: fileURL, options: .atomic)
            }
        }

        // 3. App Group UserDefaults
        for grp in candidateAppGroups {
            if let ud = UserDefaults(suiteName: grp) {
                ud.set(effURL, forKey: kServerURL)
                ud.set(effSpace, forKey: kSpaceId)
                ud.set(cleanToken, forKey: kAuthToken)
            }
        }
    }

    static func loadConfig() -> (serverURL: String, spaceId: String, authToken: String) {
        // 1. Prefer Keychain if it has a non-empty authToken (or valid config)
        var best: BridgeConfigEnvelope?
        if let kcData = keychainLoad(account: "config"),
           let kcEnv = try? JSONDecoder().decode(BridgeConfigEnvelope.self, from: kcData) {
            best = kcEnv
        }

        // 2. Check Shared Container / macOS AppSupport files for newer or richer token
        for fileURL in candidateSharedFiles {
            if let fData = try? Data(contentsOf: fileURL),
               let fEnv = try? JSONDecoder().decode(BridgeConfigEnvelope.self, from: fData) {
                if best == nil || (best?.authToken.isEmpty == true && !fEnv.authToken.isEmpty) || fEnv.updatedAt > (best?.updatedAt ?? 0) {
                    best = fEnv
                }
            }
        }

        // 3. Check App Group UserDefaults
        for grp in candidateAppGroups {
            if let ud = UserDefaults(suiteName: grp) {
                let u = (ud.string(forKey: kServerURL) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let s = (ud.string(forKey: kSpaceId) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let t = (ud.string(forKey: kAuthToken) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !t.isEmpty && (best == nil || best?.authToken.isEmpty == true) {
                    best = BridgeConfigEnvelope(
                        serverURL: u.isEmpty ? cloudServerURL : u,
                        spaceId: s.isEmpty ? "default" : s,
                        authToken: t,
                        updatedAt: 0
                    )
                } else if best == nil && !u.isEmpty {
                    best = BridgeConfigEnvelope(serverURL: u, spaceId: s.isEmpty ? "default" : s, authToken: t, updatedAt: 0)
                }
            }
        }

        let url = (best?.serverURL ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let space = (best?.spaceId ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let token = (best?.authToken ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return (
            serverURL: url.isEmpty ? cloudServerURL : url,
            spaceId: space.isEmpty ? "default" : space,
            authToken: token
        )
    }

    static func saveMatrixSnapshotData(_ data: Data) {
        // Save compact copy to Keychain (if <= 48KB) + full copy to AppGroup / shared files
        if data.count <= 49_152 {
            keychainSave(account: "matrix_snapshot", data: data)
        }
        for fileURL in candidateSharedFiles {
            let snapURL = fileURL.deletingLastPathComponent().appendingPathComponent("widget_matrix_snapshot.json")
            try? data.write(to: snapURL, options: .atomic)
        }
        for grp in candidateAppGroups {
            if let ud = UserDefaults(suiteName: grp) {
                ud.set(data, forKey: kMatrixSnapshot)
                ud.set(Date().timeIntervalSince1970, forKey: kUpdatedAt)
            }
        }
    }

    static func loadMatrixSnapshotData() -> Data? {
        for fileURL in candidateSharedFiles {
            let snapURL = fileURL.deletingLastPathComponent().appendingPathComponent("widget_matrix_snapshot.json")
            if let d = try? Data(contentsOf: snapURL), !d.isEmpty {
                return d
            }
        }
        if let kc = keychainLoad(account: "matrix_snapshot"), !kc.isEmpty {
            return kc
        }
        for grp in candidateAppGroups {
            if let d = UserDefaults(suiteName: grp)?.data(forKey: kMatrixSnapshot), !d.isEmpty {
                return d
            }
        }
        return nil
    }

    static func saveCountdownsSnapshotData(_ data: Data) {
        if data.count <= 32_768 {
            keychainSave(account: "countdowns_snapshot", data: data)
        }
        for fileURL in candidateSharedFiles {
            let snapURL = fileURL.deletingLastPathComponent().appendingPathComponent("widget_countdowns_snapshot.json")
            try? data.write(to: snapURL, options: .atomic)
        }
        for grp in candidateAppGroups {
            if let ud = UserDefaults(suiteName: grp) {
                ud.set(data, forKey: kCountdownsSnapshot)
                ud.set(Date().timeIntervalSince1970, forKey: kUpdatedAt)
            }
        }
    }

    static func loadCountdownsSnapshotData() -> Data? {
        for fileURL in candidateSharedFiles {
            let snapURL = fileURL.deletingLastPathComponent().appendingPathComponent("widget_countdowns_snapshot.json")
            if let d = try? Data(contentsOf: snapURL), !d.isEmpty {
                return d
            }
        }
        if let kc = keychainLoad(account: "countdowns_snapshot"), !kc.isEmpty {
            return kc
        }
        for grp in candidateAppGroups {
            if let d = UserDefaults(suiteName: grp)?.data(forKey: kCountdownsSnapshot), !d.isEmpty {
                return d
            }
        }
        return nil
    }
}
