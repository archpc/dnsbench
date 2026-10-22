import Foundation

// MARK: - Raw data models (parsed from run directories)
// All models are Sendable value types; decoding is tolerant so partial or
// legacy runs still load (missing fields -> nil, malformed rows skipped).

struct RankedServer: Codable, Identifiable, Hashable, Sendable {
    var ip: String
    var uMed: Double?
    var cMed: Double?
    var uP90: Double?
    var uMax: Double?
    var kdMed: Double?
    var toU: Int
    var toC: Int
    var badC: Int
    var unreach: Int?
    var strays: Int
    var failU: Int?
    var ptr: String?
    var category: String?

    var id: String { ip }

    enum CodingKeys: String, CodingKey {
        case ip, uMed = "u_med", cMed = "c_med", uP90 = "u_p90", uMax = "u_max"
        case kdMed = "kd_med", toU = "to_u", toC = "to_c", badC = "bad_c"
        case failU = "fail_u"
        case unreach, strays, ptr, category
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ip = try c.decode(String.self, forKey: .ip)
        uMed = try? c.decodeIfPresent(Double.self, forKey: .uMed)
        cMed = try? c.decodeIfPresent(Double.self, forKey: .cMed)
        uP90 = try? c.decodeIfPresent(Double.self, forKey: .uP90)
        uMax = try? c.decodeIfPresent(Double.self, forKey: .uMax)
        kdMed = try? c.decodeIfPresent(Double.self, forKey: .kdMed)
        toU = (try? c.decode(Int.self, forKey: .toU)) ?? 0
        toC = (try? c.decode(Int.self, forKey: .toC)) ?? 0
        badC = (try? c.decode(Int.self, forKey: .badC)) ?? 0
        unreach = try? c.decodeIfPresent(Int.self, forKey: .unreach)
        strays = (try? c.decode(Int.self, forKey: .strays)) ?? 0
        failU = try? c.decodeIfPresent(Int.self, forKey: .failU)
        ptr = try? c.decodeIfPresent(String.self, forKey: .ptr)
        category = try? c.decodeIfPresent(String.self, forKey: .category)
    }

    init(ip: String, uMed: Double? = nil, cMed: Double? = nil, uP90: Double? = nil,
         uMax: Double? = nil, kdMed: Double? = nil, toU: Int = 0, toC: Int = 0,
         badC: Int = 0, unreach: Int? = nil, strays: Int = 0,
         ptr: String? = nil, category: String? = nil) {
        self.ip = ip; self.uMed = uMed; self.cMed = cMed; self.uP90 = uP90
        self.uMax = uMax; self.kdMed = kdMed; self.toU = toU; self.toC = toC
        self.badC = badC; self.unreach = unreach; self.strays = strays
        self.ptr = ptr; self.category = category
    }

    // Non-optional sort keys (Optional comparables crash type inference in
    // TableColumn(value:) and make sorting ambiguous for missing values).
    var sortU: Double { uMed ?? .infinity }
    var sortC: Double { cMed ?? .infinity }
    var sortP90: Double { uP90 ?? .infinity }
    var sortMax: Double { uMax ?? .infinity }
    var sortKd: Double { kdMed ?? -1 }
    var sortCat: String { category ?? "Other" }
    var totalTimeouts: Int { toU + toC }
    /// Uncached SERVFAIL/REFUSED/other-rcode failures + invalid cached answers.
    var totalInvalid: Int { badC + (failU ?? 0) }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(ip, forKey: .ip)
        try c.encodeIfPresent(uMed, forKey: .uMed)
        try c.encodeIfPresent(cMed, forKey: .cMed)
        try c.encodeIfPresent(uP90, forKey: .uP90)
        try c.encodeIfPresent(uMax, forKey: .uMax)
        try c.encodeIfPresent(kdMed, forKey: .kdMed)
        try c.encode(toU, forKey: .toU)
        try c.encode(toC, forKey: .toC)
        try c.encode(badC, forKey: .badC)
        try c.encodeIfPresent(unreach, forKey: .unreach)
        try c.encode(strays, forKey: .strays)
        try c.encodeIfPresent(failU, forKey: .failU)
        try c.encodeIfPresent(ptr, forKey: .ptr)
        try c.encodeIfPresent(category, forKey: .category)
    }
}

struct RankedFile: Codable, Sendable {
    var generated: String?
    var nRanked: Int?
    var incompleteCached: [String]?
    var ranked: [RankedServer]

    enum CodingKeys: String, CodingKey {
        case generated, ranked
        case nRanked = "n_ranked"
        case incompleteCached = "incomplete_cached"
    }
}

/// One row of deepbench-results.jsonl: raw per-server samples.
struct ServerSamples: Codable, Identifiable, Hashable, Sendable {
    var ip: String
    var uncached: [Double]
    var cached: [Double]
    var toU: Int
    var toC: Int
    var badC: Int
    var strays: Int
    var failU: Int?
    var unreach: Int?
    var kdeltas: [Double]?
    var engine: String?
    var netsvc: String?

    enum CodingKeys: String, CodingKey {
        case ip, uncached, cached, strays, unreach, kdeltas, engine, netsvc
        case toU = "to_u", toC = "to_c", badC = "bad_c", failU = "fail_u"
    }

    var id: String { ip }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ip = try c.decode(String.self, forKey: .ip)
        uncached = (try? c.decode([Double].self, forKey: .uncached)) ?? []
        cached = (try? c.decode([Double].self, forKey: .cached)) ?? []
        toU = (try? c.decode(Int.self, forKey: .toU)) ?? 0
        toC = (try? c.decode(Int.self, forKey: .toC)) ?? 0
        badC = (try? c.decode(Int.self, forKey: .badC)) ?? 0
        strays = (try? c.decode(Int.self, forKey: .strays)) ?? 0
        failU = try? c.decodeIfPresent(Int.self, forKey: .failU)
        unreach = try? c.decodeIfPresent(Int.self, forKey: .unreach)
        kdeltas = try? c.decodeIfPresent([Double].self, forKey: .kdeltas)
        engine = try? c.decodeIfPresent(String.self, forKey: .engine)
        netsvc = try? c.decodeIfPresent(String.self, forKey: .netsvc)
    }
}

/// One row of deepbench-perquery.jsonl: a single DNS transaction.
struct QueryRecord: Codable, Identifiable, Hashable, Sendable {
    var ip: String
    var kind: String
    var name: String?
    var ms: Double?
    var rcode: Int
    var an: Int
    var kd: Double?
    /// 0-based index among the decoded records of deepbench-perquery.jsonl:
    /// stable and unique (field tuples repeat for cached probes).
    var seq: Int = 0

    enum CodingKeys: String, CodingKey {
        case ip, kind, name, ms, rcode, an, kd
    }

    var id: Int { seq }

    /// rcode -2 = timeout, per engine convention.
    var isTimeout: Bool { rcode == -2 }
    /// NOERROR with answers > 0 = valid answer.
    var isValidAnswer: Bool { rcode == 0 && an > 0 }
}

/// aggregate.json from stability.py --aggregate (statistical source of truth).
struct AggregateRecord: Codable, Sendable {
    struct AggServer: Codable, Identifiable, Hashable, Sendable {
        var ip: String
        var uMed: Double
        var wireMed: Double?
        var betweenRangeMs: Double
        var rankBand: Int
        var successU: Double?
        var successC: Double?
        var provider: String
        var ci95: [Double]?

        enum CodingKeys: String, CodingKey {
            case ip, provider, ci95, rankBand = "rank_band"
            case uMed = "u_med", wireMed = "wire_med"
            case betweenRangeMs = "between_range_ms"
            case successU = "success_u", successC = "success_c"
        }
        var id: String { ip }
    }
    struct AggProvider: Codable, Hashable, Sendable {
        var provider: String
        var uMed: Double
        var members: Int

        enum CodingKeys: String, CodingKey {
            case provider, members
            case uMed = "u_med"
        }
    }

    var windows: [String]
    var fastestObserved: [String]
    var rankBand1: [String]
    var mostStableBand1: [String]
    var bestOverall: [String]
    var materialRegressions: [String]
    var providers: [AggProvider]
    var servers: [AggServer]
    var notes: [String: String]?

    enum CodingKeys: String, CodingKey {
        case windows, servers, providers, notes
        case fastestObserved = "fastest_observed"
        case rankBand1 = "rank_band_1"
        case mostStableBand1 = "most_stable_band1"
        case bestOverall = "best_overall"
        case materialRegressions = "material_regressions"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        windows = (try? c.decode([String].self, forKey: .windows)) ?? []
        fastestObserved = (try? c.decode([String].self, forKey: .fastestObserved)) ?? []
        rankBand1 = (try? c.decode([String].self, forKey: .rankBand1)) ?? []
        mostStableBand1 = (try? c.decode([String].self, forKey: .mostStableBand1)) ?? []
        bestOverall = (try? c.decode([String].self, forKey: .bestOverall)) ?? []
        materialRegressions = (try? c.decode([String].self, forKey: .materialRegressions)) ?? []
        providers = (try? c.decode([AggProvider].self, forKey: .providers)) ?? []
        servers = (try? c.decode([AggServer].self, forKey: .servers)) ?? []
        notes = try? c.decodeIfPresent([String: String].self, forKey: .notes)
    }
}

/// pool.json: candidate pool produced by screening.
struct PoolFile: Codable, Sendable {
    struct PoolEntry: Codable, Hashable, Sendable {
        var ip: String
        var uMed: Double?
        var cMed: Double?
        var score: Double

        enum CodingKeys: String, CodingKey {
            case ip, score
            case uMed = "u_med", cMed = "c_med"
        }
    }
    struct PoolMeta: Codable, Sendable {
        var generated: String?
        var eligible: Int?
        var hijackedExcluded: Int?

        enum CodingKeys: String, CodingKey {
            case generated, eligible
            case hijackedExcluded = "hijacked_excluded"
        }
    }
    var meta: PoolMeta?
    var pool: [PoolEntry]
}
