# Diagrama entidad-relación

Generado con `python manage.py erd_mermaid --output ../docs/erd.md` (desde `backend/`). No se edita a mano: se vuelve a generar cuando cambian los modelos.

```mermaid
erDiagram
    AccountSettings {
        bigauto id PK
        auto user_id FK
        char legal_name
        char tax_id
        char sender_name
        char sender_address
        char sender_phone
        file logo
        bigauto default_template_id FK
        char default_carrier
        char default_print_format
        datetime updated_at
    }
    EmailVerification {
        bigauto id PK
        auto user_id FK
        char token UK
        boolean is_verified
        datetime created_at
        datetime updated_at
        datetime expires_at
        datetime verified_at
    }
    GroupRolePermission {
        bigauto id PK
        auto group_id FK
        bigauto permission_id FK
    }
    LoginLockout {
        bigauto id PK
        auto user_id FK
        positiveinteger failed_attempts
        datetime locked_until
    }
    PasswordChangeRequirement {
        bigauto id PK
        auto user_id FK
        boolean must_change_password
        datetime created_at
        datetime updated_at
    }
    PersonalProfile {
        bigauto id PK
        auto user_id FK
        char preferred_name
        char phone
        char job_title
        file photo
        datetime updated_at
    }
    RolePermission {
        bigauto id PK
        char key UK
        char name
        char category
    }
    SupportMessage {
        bigauto id PK
        auto user_id FK
        char subject
        text message
        char status
        text response
        datetime responded_at
        auto handled_by_id FK
        datetime created_at
        datetime updated_at
    }
    Team {
        bigauto id PK
        auto owner_id FK
        char name
        datetime created_at
    }
    TeamInvitation {
        bigauto id PK
        bigauto team_id FK
        char email
        bigauto profile_id FK
        json accesses
        boolean all_stores
        auto invited_by_id FK
        char token UK
        char status
        datetime expires_at
        datetime created_at
    }
    TeamMember {
        bigauto id PK
        bigauto team_id FK
        auto user_id FK
        char role
        bigauto profile_id FK
        json accesses
        boolean all_stores
        datetime joined_at
    }
    TeamProfile {
        bigauto id PK
        bigauto team_id FK
        char name
        json accesses
        datetime created_at
    }
    AuditLog {
        bigauto id PK
        auto actor_id FK
        char actor_email
        char category
        char action
        char target_type
        char target_id
        char target_repr
        json changes
        genericipaddress ip_address
        char user_agent
        datetime created_at
    }
    CarrierAccount {
        bigauto id PK
        auto owner_id FK
        char carrier
        char environment
        char username
        text password_encrypted
        char client_code
        json contracts
        char sender_name
        char sender_email
        char sender_phone
        char sender_document
        char origin_street
        char origin_number
        char origin_floor
        char origin_apartment
        char origin_postal_code
        char origin_city
        char origin_state
        decimal default_weight_kg
        positiveinteger default_volume_cm3
        positivesmallinteger default_length_cm
        positivesmallinteger default_width_cm
        positivesmallinteger default_height_cm
        json extra
        text secrets_encrypted
        text token_encrypted
        datetime token_expires_at
        boolean is_active
        text last_error
        datetime created_at
        datetime updated_at
    }
    CarrierPushToken {
        bigauto id PK
        bigauto account_id FK
        char key UK
        char username
        char password_hash
        datetime last_received_at
        positiveinteger received_count
        datetime created_at
    }
    CarrierShipment {
        bigauto id PK
        bigauto account_id FK
        bigauto order_id FK
        char carrier
        char contract
        char delivery_kind
        char branch_id
        char branch_name
        char tracking_number
        char group_number
        positivesmallinteger package_count
        decimal quoted_price
        char status
        char carrier_status
        datetime last_event_at
        datetime last_checked_at
        text last_error
        json raw_response
        datetime created_at
        datetime updated_at
    }
    CarrierShipmentEvent {
        bigauto id PK
        bigauto shipment_id FK
        datetime occurred_at
        char cycle
        char event
        char reason
        char sub_reason
        char status_text
        char branch
        char comment
    }
    ManualCarrier {
        bigauto id PK
        char name UK
        char tracking_url_template
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    Document {
        bigauto id PK
        auto user_id FK
        char name
        char kind
        file file
        char status
        positiveinteger item_count
        positiveinteger size_bytes
        text error_message
        json job
        json store_ids
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    UploadedLabelFile {
        bigauto id PK
        file file
        char original_filename
        char mime_type
        positiveinteger size_bytes
        auto uploaded_by_id FK
        datetime uploaded_at
    }
    IncomingWebhook {
        bigauto id PK
        auto owner_id FK
        bigauto integration_key_id FK
        char name
        slug slug UK
        char secret
        json mapping
        boolean is_active
        datetime created_at
    }
    IntegrationEvent {
        bigauto id PK
        bigauto connection_id FK
        char platform
        char event_type
        char resource_id
        json payload
        char dedupe_key
        char status
        positiveinteger attempts
        datetime next_attempt_at
        text last_error
        json result
        datetime processed_at
        datetime created_at
        datetime updated_at
    }
    IntegrationKey {
        bigauto id PK
        auto owner_id FK
        char name
        char key_hash UK
        char prefix
        boolean is_active
        datetime last_used_at
        datetime created_at
    }
    ShippingRate {
        bigauto id PK
        bigauto connection_id FK
        char option_code
        char option_name
        char postal_code_from
        char postal_code_to
        decimal weight_up_to_kg
        decimal price
        char currency
        positivesmallinteger delivery_days_min
        positivesmallinteger delivery_days_max
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    StoreConnection {
        bigauto id PK
        auto owner_id FK
        char platform
        char external_store_id
        char name
        char store_url
        text access_token_encrypted
        text refresh_token_encrypted
        datetime token_expires_at
        datetime refresh_token_expires_at
        text webhook_secret_encrypted
        char scopes
        char status
        text last_error
        char sender_name
        char sender_address
        char sender_phone
        file logo
        bigauto default_template_id FK
        positivesmallinteger label_printer_dpmm
        json preferences
        datetime connected_at
        datetime disconnected_at
        datetime last_synced_at
        datetime created_at
        datetime updated_at
    }
    StoreLabelRequest {
        bigauto id PK
        bigauto connection_id FK
        char external_label_id
        char external_fulfillment_order_id
        char status
        json payload
        file file
        char download_token
        text error_message
        datetime released_at
        datetime created_at
        datetime updated_at
    }
    WebhookDelivery {
        bigauto id PK
        bigauto endpoint_id FK
        char event
        json payload
        positiveinteger attempt
        positivesmallinteger status_code
        boolean success
        text response_body
        datetime created_at
    }
    WebhookEndpoint {
        bigauto id PK
        auto owner_id FK
        char name
        char url
        char secret
        json events
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    ElementLayout {
        bigauto id PK
        char name
        text description
        decimal width_mm
        decimal height_mm
        positiveinteger dpi
        char orientation
        json metadata
        boolean is_active
        auto created_by_id FK
        datetime created_at
        datetime updated_at
    }
    Label {
        bigauto id PK
        auto user_id FK
        bigauto template_id FK
        char name
        char client
        bigauto order_id FK
        decimal width_cm
        decimal height_cm
        json design
        file logo
        file thumbnail
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    LabelSequence {
        bigauto id PK
        auto owner_id FK
        char key
        char prefix
        positivesmallinteger padding
        biginteger current
        datetime updated_at
    }
    LabelSize {
        bigauto id PK
        char name UK
        decimal width_cm
        decimal height_cm
        boolean is_active
        datetime created_at
    }
    LabelTemplate {
        bigauto id PK
        char name
        char description
        auto owner_id FK
        boolean is_public
        decimal width_cm
        decimal height_cm
        json design
        file preview
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    LabelTemplateVersion {
        bigauto id PK
        bigauto template_id FK
        positiveinteger number
        char name
        decimal width_cm
        decimal height_cm
        json design
        char note
        positiveinteger restored_from
        auto created_by_id FK
        datetime created_at
    }
    LayoutElement {
        bigauto id PK
        bigauto layout_id FK
        char element_type
        bigauto variable_id FK
        text content
        decimal x_mm
        decimal y_mm
        decimal width_mm
        decimal height_mm
        json style
        positiveinteger order
    }
    LayoutVariable {
        bigauto id PK
        char code UK
        char label
        text description
        char data_type
        boolean is_active
        boolean is_system
        positiveinteger order
        auto created_by_id FK
        datetime created_at
    }
    AlertState {
        bigauto id PK
        char key UK
        datetime failing_since
        datetime notified_at
        text detail
        datetime updated_at
    }
    Heartbeat {
        bigauto id PK
        char name UK
        datetime last_seen
        json info
    }
    MerchantNotice {
        bigauto id PK
        auto owner_id FK
        char key
        char kind
        char title
        text detail
        char url
        char access
        json store_ids
        datetime created_at
        datetime resolved_at
        datetime dismissed_at
        datetime mailed_at
        datetime mail_attempted_at
    }
    Address {
        bigauto id PK
        auto user_id FK
        char label
        char recipient_name
        char street
        char number
        char city
        char state
        char postal_code
        char country
        char reference
        boolean is_default
        char origin
        datetime created_at
        datetime updated_at
    }
    ImportMapping {
        bigauto id PK
        auto user_id FK
        char name
        json mapping
        datetime created_at
    }
    Order {
        bigauto id PK
        auto user_id FK
        bigauto address_id FK
        char description
        char status
        char external_id
        char source
        char carrier
        char tracking_number
        char tracking_url
        bigauto store_connection_id FK
        char external_number
        char contact_email
        char contact_phone
        char shipping_option
        positiveinteger package_count
        decimal total_weight_kg
        json items
        json raw_payload
        datetime external_updated_at
        datetime created_at
        datetime updated_at
    }
    OrderImport {
        bigauto id PK
        auto user_id FK
        file file
        char original_filename
        char status
        positiveinteger total_rows
        positiveinteger imported_count
        positiveinteger skipped_count
        positiveinteger error_count
        json errors
        json mapping
        datetime created_at
        datetime updated_at
    }
    OrderStatusEvent {
        bigauto id PK
        bigauto order_id FK
        char status
        datetime created_at
    }
    PrintAgent {
        bigauto id PK
        auto owner_id FK
        char name
        char token_prefix
        char token_hash UK
        datetime last_seen_at
        char last_version
        boolean is_active
        datetime created_at
    }
    PrintJob {
        bigauto id PK
        auto owner_id FK
        bigauto printer_id FK
        bigauto agent_id FK
        bigauto document_id FK
        char status
        positiveinteger label_count
        positiveinteger attempts
        text error_message
        datetime claimed_at
        datetime finished_at
        datetime created_at
        datetime updated_at
    }
    Printer {
        bigauto id PK
        auto owner_id FK
        bigauto agent_id FK
        char name
        char format
        positivesmallinteger dpmm
        char location
        char connection
        char host
        positiveinteger port
        char queue
        boolean is_default
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    LabelImport {
        bigauto id PK
        bigauto uploaded_file_id FK
        char status
        char model_name
        json proposal
        json raw_response
        text error
        positiveinteger input_tokens
        positiveinteger output_tokens
        char request_id
        auto created_by_id FK
        datetime created_at
        datetime finished_at
    }
    Group {
        auto id PK
    }
    User {
        auto id PK
    }
    Address ||--o{ Order : "address"
    CarrierAccount ||--o{ CarrierShipment : "account"
    CarrierAccount ||--o| CarrierPushToken : "account"
    CarrierShipment ||--o{ CarrierShipmentEvent : "shipment"
    Document |o--o{ PrintJob : "document"
    ElementLayout ||--o{ LayoutElement : "layout"
    Group ||--o{ GroupRolePermission : "group"
    IntegrationKey ||--o{ IncomingWebhook : "integration_key"
    LabelTemplate |o--o{ AccountSettings : "default_template"
    LabelTemplate |o--o{ Label : "template"
    LabelTemplate |o--o{ StoreConnection : "default_template"
    LabelTemplate ||--o{ LabelTemplateVersion : "template"
    LayoutVariable |o--o{ LayoutElement : "variable"
    Order |o--o{ Label : "order"
    Order ||--o{ CarrierShipment : "order"
    Order ||--o{ OrderStatusEvent : "order"
    PrintAgent |o--o{ PrintJob : "agent"
    PrintAgent |o--o{ Printer : "agent"
    Printer |o--o{ PrintJob : "printer"
    RolePermission ||--o{ GroupRolePermission : "permission"
    StoreConnection |o--o{ IntegrationEvent : "connection"
    StoreConnection |o--o{ Order : "store_connection"
    StoreConnection ||--o{ ShippingRate : "connection"
    StoreConnection ||--o{ StoreLabelRequest : "connection"
    Team ||--o{ TeamInvitation : "team"
    Team ||--o{ TeamMember : "team"
    Team ||--o{ TeamProfile : "team"
    TeamInvitation }o--o{ StoreConnection : "stores"
    TeamMember }o--o{ StoreConnection : "stores"
    TeamProfile |o--o{ TeamInvitation : "profile"
    TeamProfile |o--o{ TeamMember : "profile"
    UploadedLabelFile ||--o{ LabelImport : "uploaded_file"
    User |o--o{ AuditLog : "actor"
    User |o--o{ ElementLayout : "created_by"
    User |o--o{ LabelImport : "created_by"
    User |o--o{ LabelTemplate : "owner"
    User |o--o{ LabelTemplateVersion : "created_by"
    User |o--o{ LayoutVariable : "created_by"
    User |o--o{ StoreConnection : "owner"
    User |o--o{ SupportMessage : "handled_by"
    User |o--o{ TeamInvitation : "invited_by"
    User ||--o{ Address : "user"
    User ||--o{ CarrierAccount : "owner"
    User ||--o{ Document : "user"
    User ||--o{ ImportMapping : "user"
    User ||--o{ IncomingWebhook : "owner"
    User ||--o{ IntegrationKey : "owner"
    User ||--o{ Label : "user"
    User ||--o{ LabelSequence : "owner"
    User ||--o{ MerchantNotice : "owner"
    User ||--o{ Order : "user"
    User ||--o{ OrderImport : "user"
    User ||--o{ PrintAgent : "owner"
    User ||--o{ PrintJob : "owner"
    User ||--o{ Printer : "owner"
    User ||--o{ SupportMessage : "user"
    User ||--o{ UploadedLabelFile : "uploaded_by"
    User ||--o{ WebhookEndpoint : "owner"
    User ||--o| AccountSettings : "user"
    User ||--o| EmailVerification : "user"
    User ||--o| LoginLockout : "user"
    User ||--o| PasswordChangeRequirement : "user"
    User ||--o| PersonalProfile : "user"
    User ||--o| Team : "owner"
    User ||--o| TeamMember : "user"
    WebhookEndpoint ||--o{ WebhookDelivery : "endpoint"
```
