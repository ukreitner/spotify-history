import axios from 'axios';

const api = axios.create({
  baseURL: 'http://127.0.0.1:8000/api',
});

export type ContentType = 'all' | 'music' | 'podcast';

export interface OverviewStats {
  total_plays: number;
  unique_artists: number;
  unique_tracks: number;
}

export interface SplitStats {
  music: {
    total_plays: number;
    unique_artists: number;
    unique_tracks: number;
  };
  podcast: {
    total_plays: number;
    unique_shows: number;
    unique_episodes: number;
  };
}

export interface ArtistStat {
  artist: string;
  play_count: number;
}

export interface GenreStat {
  genre: string;
  play_count: number;
}

export interface TrackStat {
  track_id: string;
  track: string;
  artist: string;
  play_count: number;
  image_url?: string | null;
  album?: string | null;
  spotify_url?: string | null;
}

export interface HourlyPattern {
  hour: number;
  label: string;
  plays: number;
}

export interface DailyPattern {
  day: number;
  label: string;
  plays: number;
}

export interface MonthlyPattern {
  month: number;
  label: string;
  plays: number;
}

export interface ListeningPatterns {
  by_hour: HourlyPattern[];
  by_day: DailyPattern[];
  by_month: MonthlyPattern[];
  peak_hour: number;
  peak_hour_label: string;
  peak_day: number;
  peak_day_label: string;
}

export interface ListeningStreaks {
  current_streak: number;
  longest_streak: number;
  total_listening_days: number;
  streak_start: string | null;
  longest_streak_start: string;
  longest_streak_end: string;
}

export interface ForgottenGem {
  track_id: string;
  track: string;
  artist: string;
  play_count: number;
  last_played: string;
  days_since_played: number;
  score: number;
  image_url?: string | null;
  album?: string | null;
  preview_url?: string | null;
  spotify_url?: string | null;
}

export interface NewArtist {
  artist_id: string;
  artist_name: string;
  sample_track?: string;
  sample_track_id?: string;
  preview_url?: string | null;
  image_url?: string | null;
  genres?: string[];
  popularity?: number;
  relevance?: number;
  seed_artist?: string;
  found_via_genre?: string;
}

export interface MoodTrack {
  track_id: string;
  track: string;
  artist: string;
  album?: string;
  image_url?: string | null;
  preview_url?: string | null;
  spotify_url?: string | null;
  duration_ms?: number;
  score?: number;
  energy?: number;
  valence?: number;
  tempo?: number;
  play_count?: number;
}

export interface Mood {
  id: string;
  name: string;
  description: string;
}

export interface PodcastStats {
  total_plays: number;
  unique_shows: number;
  unique_episodes: number;
}

export interface PodcastShow {
  show: string;
  episode_count: number;
}

export interface PodcastEpisode {
  episode: string;
  show?: string;
  play_count?: number;
  last_played?: string;
  days_since_played?: number;
}

// Stats endpoints
export const getOverview = (contentType: ContentType = 'all') =>
  api.get<OverviewStats>('/stats/overview', { params: { content_type: contentType } }).then(r => r.data);

export const getOverviewSplit = () =>
  api.get<SplitStats>('/stats/overview/split').then(r => r.data);

export const getTopArtists = (limit = 20, contentType: ContentType = 'all') =>
  api.get<ArtistStat[]>('/stats/artists', { params: { limit, content_type: contentType } }).then(r => r.data);

export const getTopGenres = (limit = 20, contentType: ContentType = 'all') =>
  api.get<GenreStat[]>('/stats/genres', { params: { limit, content_type: contentType } }).then(r => r.data);

export const getTopTracks = (limit = 20, contentType: ContentType = 'all') =>
  api.get<TrackStat[]>('/stats/tracks', { params: { limit, content_type: contentType } }).then(r => r.data);

export const getListeningPatterns = (contentType: ContentType = 'all') =>
  api.get<ListeningPatterns>('/stats/patterns', { params: { content_type: contentType } }).then(r => r.data);

export const getListeningStreaks = (contentType: ContentType = 'all') =>
  api.get<ListeningStreaks>('/stats/streaks', { params: { content_type: contentType } }).then(r => r.data);

// Podcast endpoints
export const getPodcastStats = () =>
  api.get<PodcastStats>('/podcasts/stats').then(r => r.data);

export const getTopShows = (limit = 20) =>
  api.get<PodcastShow[]>('/podcasts/shows', { params: { limit } }).then(r => r.data);

export const getShowEpisodes = (show: string, limit = 50) =>
  api.get<PodcastEpisode[]>(`/podcasts/episodes/${encodeURIComponent(show)}`, { params: { limit } }).then(r => r.data);

export const getRecentEpisodes = (limit = 20) =>
  api.get<PodcastEpisode[]>('/podcasts/recent', { params: { limit } }).then(r => r.data);

export const getPodcastBacklog = (limit = 20) =>
  api.get<PodcastEpisode[]>('/podcasts/backlog', { params: { limit } }).then(r => r.data);

// Recommendation endpoints
export const getForgottenGems = (contentType: ContentType = 'music') =>
  api.get<ForgottenGem[]>('/recommendations/gems', { params: { content_type: contentType } }).then(r => r.data);

export const getNewArtists = (limit = 20) =>
  api.get<NewArtist[]>('/recommendations/discover', { params: { limit } }).then(r => r.data);

export const getMoodPlaylist = (mood: string, limit = 25) =>
  api.get<MoodTrack[]>(`/recommendations/mood/${mood}`, { params: { limit } }).then(r => r.data);

export const getAvailableMoods = () =>
  api.get<Mood[]>('/recommendations/moods').then(r => r.data);

export const createPlaylist = (name: string, trackIds: string[], description = '') =>
  api.post<{ id: string; name: string; url: string }>('/playlists/create', {
    name,
    track_ids: trackIds,
    description
  }).then(r => r.data);

// === Vibe Playlist Types ===

export interface VibeTrack {
  track_id: string;
  track: string;
  artist: string;
  image_url?: string | null;
  preview_url?: string | null;
  spotify_url?: string | null;
  source: 'history' | 'discovery' | 'unknown';
  discovered_via?: string | null;
  coherence_score: number;
  energy?: number | null;
  valence?: number | null;
  tempo?: number | null;
  play_count: number;
}

export interface VibeProfile {
  anchor_count: number;
  has_audio_features: boolean;
  top_genres: string[];
  target_energy?: number | null;
  target_valence?: number | null;
  target_tempo?: number | null;
}

export interface FlowStats {
  avg_transition_cost: number;
  max_transition_cost: number;
  smooth_transitions: number;
  jarring_transitions: number;
  total_transitions: number;
}

export interface VibePlaylistResult {
  tracks: VibeTrack[];
  vibe_profile: VibeProfile;
  flow_stats: FlowStats;
  counts: {
    history: number;
    discovery: number;
    total: number;
  };
}

export interface AnchorTrack {
  track_id: string;
  track: string;
  artist: string;
  image_url?: string | null;
  source: 'recent' | 'history' | 'spotify';
  play_count?: number;
}

export type FlowMode = 'smooth' | 'energy_arc' | 'shuffle';

// === Vibe Playlist API Calls ===

export interface VibePlaylistRequest {
  anchor_track_ids: string[];
  track_count?: number;
  discovery_ratio?: number;
  flow_mode?: FlowMode;
  exclude_artists?: string[];
}

export const generateVibePlaylist = (request: VibePlaylistRequest) =>
  api.post<VibePlaylistResult>('/recommendations/vibe', request).then(r => r.data);

export const searchSpotifyTracks = (query: string, limit = 20) =>
  api.get<AnchorTrack[]>('/tracks/search', { params: { q: query, limit } }).then(r => r.data);

export const getRecentTracks = (days = 7, limit = 20) =>
  api.get<AnchorTrack[]>('/tracks/recent', { params: { days, limit } }).then(r => r.data);

export const searchHistoryTracks = (query: string, limit = 20) =>
  api.get<AnchorTrack[]>('/tracks/history/search', { params: { q: query, limit } }).then(r => r.data);
