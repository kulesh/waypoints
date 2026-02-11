"""Shared style constants for flight plan widgets and modals."""

FLIGHT_PLAN_TREE_CSS = """
FlightPlanTree {
    height: 1fr;
    padding: 0;
    width: 1fr;
    overflow-x: hidden;
    scrollbar-gutter: stable;
    scrollbar-size: 1 1;
    scrollbar-background: $surface;
    scrollbar-color: $surface-lighten-2;
}

FlightPlanTree > .tree--guides {
    color: $text-muted;
}

FlightPlanTree > .tree--guides-selected {
    color: $text-muted;
}

FlightPlanTree > .tree--cursor {
    background: $surface-lighten-1;
    color: $text;
    text-style: none;
}

FlightPlanTree:focus > .tree--cursor {
    background: $surface-lighten-2;
    color: $text;
    text-style: bold;
}

FlightPlanTree > .tree--highlight {
    text-style: none;
}

FlightPlanTree > .tree--highlight-line {
    background: $surface-lighten-1;
}
"""

FLIGHT_PLAN_PANEL_CSS = """
FlightPlanPanel {
    width: 1fr;
    height: 100%;
    border-right: solid $surface-lighten-1;
}

FlightPlanPanel .panel-title {
    text-style: bold;
    color: $text;
    padding: 1;
    border-bottom: solid $surface-lighten-1;
}

FlightPlanPanel .legend {
    dock: bottom;
    height: auto;
    padding: 1;
    border-top: solid $surface-lighten-1;
    color: $text-muted;
}
"""

WAYPOINT_PREVIEW_PANEL_CSS = """
WaypointPreviewPanel {
    width: 1fr;
    height: 100%;
    padding: 1 2;
}

WaypointPreviewPanel .panel-title {
    text-style: bold;
    color: $text;
    padding-bottom: 1;
    border-bottom: solid $surface-lighten-1;
    margin-bottom: 1;
}

WaypointPreviewPanel .placeholder {
    color: $text-muted;
    text-style: italic;
}

WaypointPreviewPanel .wp-title {
    text-style: bold;
    margin-bottom: 1;
}

WaypointPreviewPanel .wp-objective {
    color: $text;
    margin-bottom: 1;
}

WaypointPreviewPanel .wp-meta {
    color: $text-muted;
    margin-bottom: 0;
}

WaypointPreviewPanel .wp-hint {
    color: $text-disabled;
    text-style: italic;
    margin-top: 2;
}
"""

WAYPOINT_MODAL_BASE_CSS = """
WaypointModalBase {
    align: center middle;
    background: $surface 60%;
}

WaypointModalBase > Vertical {
    width: 70;
    height: auto;
    max-height: 80%;
    background: $surface;
    border: solid $surface-lighten-2;
    padding: 1 2;
}

WaypointModalBase .modal-title {
    text-style: bold;
    color: $text;
    text-align: center;
    padding: 1 0;
    margin-bottom: 1;
    border-bottom: solid $surface-lighten-1;
}

WaypointModalBase .modal-content {
    height: auto;
    max-height: 40;
    padding: 0;
    scrollbar-gutter: stable;
    scrollbar-size: 1 1;
    scrollbar-background: transparent;
    scrollbar-color: $surface-lighten-2;
}

WaypointModalBase .modal-actions {
    dock: bottom;
    height: auto;
    padding: 1 0 0 0;
    margin-top: 1;
    border-top: solid $surface-lighten-1;
    align: center middle;
}

WaypointModalBase Button {
    margin: 0 1;
    min-width: 10;
    height: 3;
    background: $surface-lighten-1;
}
"""

WAYPOINT_DETAIL_MODAL_CSS = """
WaypointDetailModal > Vertical {
    width: 70%;
    max-width: 80;
    padding: 0 1;
}

WaypointDetailModal .modal-title {
    padding: 0;
    margin: 0 0 1 0;
}

WaypointDetailModal .modal-content {
    height: 1fr;
    min-height: 5;
    max-height: 50;
    margin: 0;
}

WaypointDetailModal .modal-content Markdown {
    margin: 0;
    padding: 0;
}

WaypointDetailModal .modal-actions {
    padding: 0;
}

WaypointDetailModal Button {
    min-width: 6;
    color: $text-muted;
}

WaypointDetailModal Button:hover {
    background: $surface-lighten-2;
    color: $text;
}
"""

CONFIRM_DELETE_MODAL_CSS = """
ConfirmDeleteModal > Vertical {
    width: 60;
    max-height: 24;
    border-top: solid $error;
}

ConfirmDeleteModal .modal-title {
    border-bottom: none;
}

ConfirmDeleteModal .waypoint-info {
    margin-bottom: 1;
    color: $text-muted;
}

ConfirmDeleteModal .warning {
    color: $warning;
    margin-top: 1;
    padding: 0;
}

ConfirmDeleteModal Button#btn-delete {
    background: $error-darken-2;
}

ConfirmDeleteModal Button#btn-cancel {
    background: $surface-lighten-1;
}
"""

WAYPOINT_EDIT_MODAL_CSS = """
WaypointEditModal > Vertical {
    width: 80%;
    max-width: 90;
    max-height: 85%;
}

WaypointEditModal .form-content {
    height: auto;
    max-height: 45;
    padding: 0;
    scrollbar-gutter: stable;
    scrollbar-size: 1 1;
    scrollbar-background: transparent;
    scrollbar-color: $surface-lighten-2;
}

WaypointEditModal .field-label {
    margin-top: 1;
    margin-bottom: 0;
    color: $text-muted;
}

WaypointEditModal Input {
    margin-bottom: 1;
    background: $surface-lighten-1;
    border: none;
}

WaypointEditModal Input:focus {
    background: $surface-lighten-2;
    border: none;
}

WaypointEditModal TextArea {
    height: 6;
    margin-bottom: 1;
    background: $surface-lighten-1;
    border: none;
}

WaypointEditModal TextArea:focus {
    background: $surface-lighten-2;
    border: none;
}

WaypointEditModal .criteria-area {
    height: 8;
}

WaypointEditModal .hint {
    color: $text-disabled;
    text-style: italic;
    margin-bottom: 1;
}

WaypointEditModal Button#btn-save {
    background: $success-darken-2;
}

WaypointEditModal Button#btn-cancel {
    background: $surface-lighten-1;
}
"""

BREAKDOWN_PREVIEW_MODAL_CSS = """
BreakDownPreviewModal > Vertical {
    width: 80%;
    max-width: 90;
}

BreakDownPreviewModal .parent-info {
    color: $text-muted;
    margin-bottom: 1;
    padding-bottom: 1;
    border-bottom: dashed $surface-lighten-1;
}

BreakDownPreviewModal .sub-waypoint {
    margin-bottom: 1;
    padding: 1;
    background: $surface-lighten-1;
}

BreakDownPreviewModal .sub-title {
    text-style: bold;
    color: $text;
}

BreakDownPreviewModal .sub-objective {
    color: $text-muted;
    margin-top: 0;
}

BreakDownPreviewModal Button#btn-confirm {
    background: $success-darken-2;
}

BreakDownPreviewModal Button#btn-cancel {
    background: $surface-lighten-1;
}
"""

ADD_WAYPOINT_MODAL_CSS = """
AddWaypointModal > Vertical {
    max-height: 24;
}

AddWaypointModal .modal-label {
    color: $text-muted;
    padding: 0 0 1 0;
}

AddWaypointModal TextArea {
    height: 6;
    margin-bottom: 1;
    background: $surface-lighten-1;
    border: none;
}

AddWaypointModal TextArea:focus {
    background: $surface-lighten-2;
    border: none;
}
"""

DEBUG_WAYPOINT_MODAL_CSS = """
DebugWaypointModal > Vertical {
    max-height: 24;
}

DebugWaypointModal .modal-label {
    color: $text-muted;
    padding: 0 0 1 0;
}

DebugWaypointModal TextArea {
    height: 6;
    margin-bottom: 1;
    background: $surface-lighten-1;
    border: none;
}

DebugWaypointModal TextArea:focus {
    background: $surface-lighten-2;
    border: none;
}
"""

ADD_WAYPOINT_PREVIEW_MODAL_CSS = """
AddWaypointPreviewModal > Vertical {
    max-height: 32;
}

AddWaypointPreviewModal .modal-content {
    max-height: 18;
}

AddWaypointPreviewModal .waypoint-id {
    text-style: bold;
    color: $primary;
}

AddWaypointPreviewModal .waypoint-title {
    text-style: bold;
    color: $text;
    margin-top: 1;
}

AddWaypointPreviewModal .waypoint-objective {
    color: $text-muted;
    margin-top: 1;
}

AddWaypointPreviewModal .section-label {
    color: $text;
    text-style: bold;
    margin-top: 1;
}

AddWaypointPreviewModal .criteria-item {
    color: $text-muted;
    padding-left: 2;
}

AddWaypointPreviewModal .insert-info {
    color: $text-disabled;
    text-style: italic;
    margin-top: 1;
}
"""

REPRIORITIZE_PREVIEW_MODAL_CSS = """
ReprioritizePreviewModal > Vertical {
    width: 90%;
    max-width: 100;
    max-height: 85%;
}

ReprioritizePreviewModal .rationale {
    color: $text-muted;
    text-style: italic;
    padding: 1;
    margin-bottom: 1;
    background: $surface-lighten-1;
}

ReprioritizePreviewModal .columns-container {
    height: auto;
    max-height: 30;
}

ReprioritizePreviewModal .order-column {
    width: 1fr;
    height: auto;
    padding: 0 1;
}

ReprioritizePreviewModal .column-title {
    text-style: bold;
    color: $text;
    padding-bottom: 1;
    border-bottom: dashed $surface-lighten-1;
    margin-bottom: 1;
}

ReprioritizePreviewModal .waypoint-item {
    padding: 0;
    height: auto;
}

ReprioritizePreviewModal .waypoint-moved {
    color: $warning;
    text-style: bold;
}

ReprioritizePreviewModal .arrow-column {
    width: 5;
    text-align: center;
    padding-top: 3;
    color: $text-muted;
}

ReprioritizePreviewModal Button {
    min-width: 12;
}

ReprioritizePreviewModal Button#btn-confirm {
    background: $success-darken-2;
}

ReprioritizePreviewModal Button#btn-cancel {
    background: $surface-lighten-1;
}
"""
