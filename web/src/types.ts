export type Bar={trade_date:string;period_key?:string;open:number;high:number;low:number;close:number;volume:number;amount:number;is_forming?:boolean;status?:"provisional"|"confirmed"};
export type MacdPoint={trade_date:string;dif:number;dea:number;histogram:number};
export type MaPoint={trade_date:string;values:Record<string,number|null>;[key:string]:unknown};
export type BollPoint={trade_date:string;middle:number|null;upper:number|null;lower:number|null};
export type Quote={trade_date:string;latest:number;change:number|null;change_pct:number|null;previous_close:number|null;open:number;high:number;low:number;volume:number;amount:number|null;amplitude_pct:number|null;market_status:string;quote_time?:string|null;source?:string;status?:"success"|"unavailable"|"error"|"stale"|"historical"};
export type PeriodRefresh={period_key:string|null;state:"cached"|"provisional"|"confirmed"|"stale"|"unavailable";is_forming:boolean;server_time:string;latest_data_at:string|null;source_revision?:string|null;request_started_at?:string|null;last_success_at:string|null;next_period_finalize_at?:string|null;is_today:boolean;error:string|null};
export type IntradayRefresh={server_time:string;phase:"auction"|"trading"|"lunch"|"closed"|"unknown";market_status:string;next_transition_at:string|null;next_bar_finalize_at?:string|null;next_period_finalize_at?:string|null;data_date:string|null;latest_data_at:string|null;last_success_at:string|null;result:"cached"|"success"|"failed"|"stale";error:string|null;calendar_error?:string|null;is_today:boolean;period_refresh?:PeriodRefresh};
export type PathPoint={trade_date:string;price:number;clipped?:boolean};

export type StructureStatus="formed"|"closed"|"broken"|"confirmed"|"provisional"|"candidate"|"invalidated"|"undetermined"|"truncated";
export interface StructureBase {id:string;kind:"pen"|"component"|"center";ordinal:number;level:number;status:StructureStatus|string;start_date:string;end_date:string;start_price?:number;end_price?:number;direction?:"up"|"down"|string;confirmed_at?:string|null;continuous_range_id?:number;sequence_id?:number;structure_sequence_id?:string;source_pen_ids?:string[];evidence?:Record<string,unknown>|string[];active?:boolean;path_points?:PathPoint[];endpoint_points?:PathPoint[];}
export interface Pen extends StructureBase {kind:"pen";start_price:number;end_price:number;low?:number;high?:number;}
export interface Component extends StructureBase {kind:"component";role:string;start_price:number;end_price:number;low:number;high:number;source_unit_ids:string[];owner_id?:string|null;}
export interface Center extends StructureBase {kind:"center";family_id:string;revision_no:number;previous_revision_id?:string|null;zd:number;zg:number;fixed_zd:number|null;fixed_zg:number|null;dd:number;gg:number;fluctuation_dd:number;fluctuation_gg:number;core_start_date?:string;core_end_date?:string;entry_direction?:string|null;core_formation_pattern?:string;departure_direction?:string|null;entry_component_id?:string|null;departure_component_id?:string|null;retest_component_id?:string|null;entry_unit_ids:string[];core_unit_ids:string[];extension_unit_ids:string[];peripheral_unit_ids:string[];departure_unit_ids:string[];retest_unit_ids:string[];owned_unit_ids:string[];context_unit_ids:string[];child_center_ids:string[];formation_modes:string[];absorbed_into_family_id?:string;candidate_source_ids?:string[];child_segment_ids?:string[];decomposition_proof?:{boundary_status:string;available_at:string;evidence_available_at?:string;segments:SegmentProof[]};}
export type PromotionMissingEvidence={code:string;[key:string]:unknown};
export interface SegmentProof {id:string;family_id:string;revision_no:number;previous_revision_id?:string|null;active:boolean;level:number;source_kind:"local_pen_group";status:"provisional"|"confirmed";direction:"up"|"down";start_date:string;end_date:string;start_price:number;end_price:number;low:number;high:number;source_unit_ids:string[];source_pen_ids:string[];center_witnesses?:Record<string,unknown>[];level_evidence_ids?:string[];boundary_mode?:string;completion_evidence_id?:string|null;observed_at?:string;evidence_available_at:string;recursive_eligible:boolean;parent_proof_id?:string;selection_status?:"selected"|"rejected";}
export interface PromotionCandidate {id:string;kind:"promotion_candidate";family_id:string;revision_no:number;previous_revision_id?:string|null;active:boolean;candidate_source:"extension_decomposition"|"expansion_decomposition";child_level:number;parent_level:number;status:"unresolved"|"dynamic"|"fixed";source_entity_ids:string[];required_unit_ids:string[];search_unit_ids:string[];start_date:string;end_date:string;observed_at:string;evidence_available_at:string;proof_ids:string[];selected_parent_family_id?:string|null;selected_parent_proof_id?:string|null;selected_segment_proof_ids?:string[];missing_evidence:PromotionMissingEvidence[];rejected_proofs:Record<string,unknown>[];}
export type StructureEntity=Pen|Component|Center;
export type CenterDisplayReference={revision_id:string;display_role:"active"|"constituent";parent_revision_ids:string[]};
export interface Center {display_role?:"active"|"constituent";parent_revision_ids?:string[];temporary_evidence?:boolean;selected_evidence?:boolean;unit_kind?:"pen"|"segment_proof"|"center_revision";z_unit_ids:string[];z_direction?:"up"|"down"|null;formed_at?:string;promotion_confirmed_at?:string|null;connection_component_ids:string[];overlap_witness_unit_ids:string[];missing_evidence:string[];context_low?:number;context_high?:number;child_segment_ids?:string[];boundary_status?:"dynamic"|"fixed"|"unresolved";}

export type Anchor={trade_date:string;price:number};
export type DrawingStyle={color?:string;width?:number;line_type?:"solid"|"dashed"|"dotted";opacity?:number;fill_color?:string;fill_opacity?:number};
export type Drawing={id:number;symbol:string;timeframe:string;object_type:"segment"|"line"|"rectangle";start_anchor:Anchor;end_anchor:Anchor;style:DrawingStyle;label:string;visible:boolean;anchor_status?:string};
export type Coverage={timeframe?:string;status?:string;range_start?:string;range_end?:string;bar_count?:number;complete?:boolean;calendar_verified?:boolean;missing_sessions?:string[];partial_sessions?:string[];duplicate_bars?:string[];invalid_bars?:string[];continuous_ranges?:{start_date:string;end_date:string;session_count?:number}[];gap_tasks?:{start_date:string;end_date:string}[]};
export type PenDiagnostic={stage:string;start_date:string;end_date:string;evaluated_at:string;direction:string;start_fractal:Record<string,unknown>;end_fractal:Record<string,unknown>;start_right:Record<string,unknown>;candidate_extreme:number|null;reason:string;secondary_reason?:string|null;outcome:string;range_index?:number};

export type ChartMeta={calculation_profile?:CalculationProfile;symbol:string;timeframe:string;definition_version:string;calculator_fingerprint:string;structure_version:string;market_version?:string;run_id?:number;available:boolean;active_level:number;max_level:number;diagnostics:boolean;preview?:boolean;persisted?:boolean;sampling_coverage?:Coverage;[key:string]:unknown};
export type CalculationProfile = "pen_centers_l2" | "pen_centers_only";
export type DailyL2Projection = {id:string;revision_id:string;family_id:string;ordinal:number;level:2;display_role:"active"|"constituent";parent_revision_ids:string[];status:string;active:boolean;start_date:string;end_date:string;zd:number;zg:number;source_timeframe:"d";target_start_date:string;target_end_date:string;clipped_start:boolean;clipped_end:boolean};
export type DailyL2Source = {timeframe:"d";symbol:string;adjustflag:string;run_id?:number;definition_version:string;calculator_fingerprint:string;market_version:string;structure_version:string;source_cutoff:string|null};
export type DailyL2Overlay = {status:"ready"|"stale"|"unavailable";error:string|null;source:DailyL2Source|null;centers:DailyL2Projection[]};
export type ChartOverlays = {daily_l2:DailyL2Overlay};
export type CenterCandidate=Record<string, any> & {id:string;family_id:string;level:number;status:string;source_unit_ids:string[];context_unit_ids:string[];rejection_code?:string|null};
export type ChartApiResponse={overlays?:ChartOverlays;meta:ChartMeta;market:{symbol:string;timeframe:string;adjustflag:string;bars:Bar[];previous_close?:number|null;quote?:Quote|null;forming_bar?:{trade_date:string;is_forming:boolean;status:"provisional"|"confirmed";finalize_at?:string}|null;intraday_refresh?:IntradayRefresh;period_refresh?:PeriodRefresh};structure:{display_centers?:CenterDisplayReference[];display_center_levels?:number[];pens:Pen[];components:Component[];centers:Center[];center_revisions:Center[];center_candidates?:CenterCandidate[];center_candidate_revisions?:CenterCandidate[];segment_proofs?:SegmentProof[];segment_proof_revisions?:SegmentProof[];promotion_candidates:PromotionCandidate[];promotion_candidate_revisions?:PromotionCandidate[];relations:Record<string,unknown>[];issues:Record<string,unknown>[];levels:number[];unassigned_by_level:Record<string,string[]>;pen_diagnostics?:PenDiagnostic[]};indicators:{macd:MacdPoint[];ma?:MaPoint[];boll?:BollPoint[]};drawings:{items:Drawing[];version:string};pagination:{has_more:boolean;next_before?:string|null}};

export type StructureDeltaPayload = {
  replace_from?: string | null;
  removed_ids: Record<string, string[]>;
  meta: Partial<ChartMeta>;
  structure: ChartApiResponse["structure"];
};
export type ChartRealtimeResponse = {
  symbol: string;
  timeframe: string;
  adjustflag: string;
  market_version: string;
  formal_market_version: string;
  structure_version: string;
  structure_changed: boolean;
  bar_upserts: Bar[];
  indicator_upserts: {macd: MacdPoint[]; ma?: MaPoint[]; boll?: BollPoint[]};
  quote?: Quote | null;
  forming_bar?: ChartData["forming_bar"];
  intraday_refresh: IntradayRefresh;
  period_refresh?: PeriodRefresh;
  structure_update?: StructureDeltaPayload | null;
};

export type ChartData={calculation_profile?:CalculationProfile;overlays?:ChartOverlays;display_centers?:CenterDisplayReference[];display_center_levels?:number[];symbol:string;timeframe:string;adjustflag:string;previous_close?:number|null;quote?:Quote|null;intraday_refresh?:IntradayRefresh;period_refresh?:PeriodRefresh;forming_bar?:{trade_date:string;is_forming:boolean;status:"provisional"|"confirmed";finalize_at?:string}|null;bars:Bar[];pens:Pen[];pen_diagnostics?:PenDiagnostic[];centers:Center[];center_revisions:Center[];center_candidates?:CenterCandidate[];center_candidate_revisions?:CenterCandidate[];components:Component[];segment_proofs?:SegmentProof[];segment_proof_revisions?:SegmentProof[];promotion_candidates:PromotionCandidate[];promotion_candidate_revisions:PromotionCandidate[];relations:Record<string,unknown>[];issues:Record<string,unknown>[];levels:number[];unassigned_by_level:Record<string,string[]>;center_levels:number[];drawings:Drawing[];drawings_version:string;indicators:{macd:MacdPoint[];ma?:MaPoint[];boll?:BollPoint[]};has_more:boolean;next_before?:string;available:boolean;stale_reason?:string;definition_version:string;calculator_fingerprint:string;structure_version:string;market_version?:string;run_id?:number;active_structure_level:number;max_available_center_level:number;coverage?:Coverage;structure_preview?:boolean;structure_persisted?:boolean};

export type Stock={symbol:string;name:string;sync_status?:string;last_sync?:string;market?:{latest?:number;change_pct?:number;ranges:Record<string,unknown>};quote?:import("./watchlistQuotes").WatchlistQuote;quote_time?:string|null;quote_status?:string;quote_source?:string};
export type WatchlistGroup={id:number;name:string;sort_order:number;member_count:number};
export type WatchlistMembership={group_id:number;symbol:string;sort_order:number};
export type WatchlistResponse={stocks:Stock[];groups:WatchlistGroup[];memberships:WatchlistMembership[];section_order?:string[]};
export type ChartLayoutPreset="main"|"main-volume"|"full";
export type PanelVisibility={left:boolean;right:boolean};
export type SubplotVisibility=boolean[];
export type SecurityCandidate={symbol:string;market_code:string;name:string;market:string;trade_status:string;selected:boolean};
export type SecuritySearchResult={query:string;items:SecurityCandidate[];catalog_date?:string;refreshed_at?:string;count:number};

export interface Center {
  formation_type?: "pullback" | "rebound" | "undetermined";
  formation_stage?: "directional" | "origin_overlap" | "boundary_candidate";
  process_direction_at_formation?: "up" | "down" | "unknown";
  direction_established_at?: string; available_at?: string;
  direction_context?: {process_direction: string; reason: string; available_at: string; source_unit_ids: string[]} | null;
  z_high_min?: number; z_low_max?: number; touch_unit_ids?: string[];
  boundary_status?: "dynamic" | "fixed" | "unresolved";
  ownership_scope?: string; ownership_commit_at?: string; closure_reason?: string;
  successor_center_id?: string|null; predecessor_center_id?: string|null;
  decomposition_proof?: {boundary_status: string; available_at: string; evidence_available_at?:string; segments: SegmentProof[]};
}
