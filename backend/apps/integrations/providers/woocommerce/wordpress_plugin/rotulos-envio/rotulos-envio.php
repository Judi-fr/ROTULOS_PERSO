<?php
/**
 * Plugin Name:       Rótulos de envío
 * Description:       Print shipping labels, the pickup sheet or ship with Andreani for the selected orders straight from the WooCommerce order list, and quote shipping at checkout from your rates table.
 * Version:           1.3.0
 * Requires at least: 6.5
 * Requires PHP:      7.4
 * Requires Plugins:  woocommerce
 * WC requires at least: 8.0
 * WC tested up to:     11.1
 * License:           GPLv2 or later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       rotulos-envio
 * Domain Path:       /languages
 *
 * How it works: the merchant ticks orders in WooCommerce → Orders, picks one
 * of our bulk actions, and this plugin asks the Rótulos service for a
 * short-lived link to the PDF, then sends the browser there (in a new tab):
 * - "Print shipping labels": the labels.
 * - "Print pickup sheet": the list the carrier signs when picking up.
 * - "Ship with Andreani": the service creates the Andreani shipments with the
 *   merchant's own Andreani account (set up in the service), loads the
 *   tracking numbers into the orders, and returns the label + Andreani's
 *   label to print.
 *
 * Nothing to configure by hand: the service writes this plugin's settings
 * (endpoint URL, store id, shared secret) through the WooCommerce REST API
 * with the keys the merchant granted it — see the `rotulos` settings group
 * below. Requests to the service are signed with that secret (HMAC-SHA256,
 * base64), the same scheme WooCommerce uses to sign its own webhooks.
 *
 * Source strings are English (WordPress.org convention); the Spanish texts
 * merchants see come from languages/.
 */

defined( 'ABSPATH' ) || exit;

const ROTULOS_BULK_ACTION   = 'rotulos_print';
// Bulk action => the service's action. The first one keeps its old key so
// existing installs and habits don't change.
const ROTULOS_ACTIONS = array(
	ROTULOS_BULK_ACTION => 'labels',
	'rotulos_manifest'  => 'manifest',
	'rotulos_dispatch'  => 'dispatch',
);
const ROTULOS_OPTION_URL    = 'rotulos_print_link_url';
const ROTULOS_OPTION_STORE  = 'rotulos_store_id';
const ROTULOS_OPTION_SECRET = 'rotulos_secret';
const ROTULOS_OPTION_RATES  = 'rotulos_rates_url';

require_once __DIR__ . '/includes/shipping.php';

add_action(
	'init',
	function () {
		load_plugin_textdomain( 'rotulos-envio', false, dirname( plugin_basename( __FILE__ ) ) . '/languages' );
	}
);

add_action(
	'before_woocommerce_init',
	function () {
		if ( class_exists( \Automattic\WooCommerce\Utilities\FeaturesUtil::class ) ) {
			\Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility( 'custom_order_tables', __FILE__, true );
		}
	}
);

// --- Settings written by the service (WooCommerce REST: /wc/v3/settings/rotulos) ---
// `option_key` is required: without it WooCommerce answers 200 and stores nothing.

add_filter(
	'woocommerce_settings_groups',
	function ( $groups ) {
		$groups[] = array(
			'id'          => 'rotulos',
			'label'       => __( 'Shipping labels', 'rotulos-envio' ),
			'description' => __( 'Settings written by the shipping labels service when the store is connected.', 'rotulos-envio' ),
		);
		return $groups;
	}
);

add_filter(
	'woocommerce_settings-rotulos',
	function ( $settings ) {
		$settings[] = array(
			'id'         => ROTULOS_OPTION_URL,
			'option_key' => ROTULOS_OPTION_URL,
			'label'      => __( 'Print request URL', 'rotulos-envio' ),
			'type'       => 'text',
			'default'    => '',
		);
		$settings[] = array(
			'id'         => ROTULOS_OPTION_RATES,
			'option_key' => ROTULOS_OPTION_RATES,
			'label'      => __( 'Shipping quote URL', 'rotulos-envio' ),
			'type'       => 'text',
			'default'    => '',
		);
		$settings[] = array(
			'id'         => ROTULOS_OPTION_STORE,
			'option_key' => ROTULOS_OPTION_STORE,
			'label'      => __( 'Store number in the service', 'rotulos-envio' ),
			'type'       => 'text',
			'default'    => '',
		);
		$settings[] = array(
			'id'         => ROTULOS_OPTION_SECRET,
			'option_key' => ROTULOS_OPTION_SECRET,
			'label'      => __( 'Shared secret', 'rotulos-envio' ),
			'type'       => 'password',
			'default'    => '',
		);
		return $settings;
	}
);

// --- Bulk action, on both order list screens (HPOS and legacy posts) ---

function rotulos_add_bulk_action( $actions ) {
	if ( current_user_can( 'edit_shop_orders' ) ) {
		$actions[ ROTULOS_BULK_ACTION ] = __( 'Print shipping labels', 'rotulos-envio' );
		$actions['rotulos_manifest']    = __( 'Print pickup sheet', 'rotulos-envio' );
		$actions['rotulos_dispatch']    = __( 'Ship with Andreani (labels + tracking)', 'rotulos-envio' );
	}
	return $actions;
}
add_filter( 'bulk_actions-woocommerce_page_wc-orders', 'rotulos_add_bulk_action' );
add_filter( 'bulk_actions-edit-shop_order', 'rotulos_add_bulk_action' );

function rotulos_handle_bulk_action( $redirect_to, $action, $ids ) {
	if ( ! array_key_exists( $action, ROTULOS_ACTIONS ) ) {
		return $redirect_to;
	}
	$service_action = ROTULOS_ACTIONS[ $action ];
	if ( ! current_user_can( 'edit_shop_orders' ) ) {
		return rotulos_fail( $redirect_to, __( 'You are not allowed to print shipping labels.', 'rotulos-envio' ) );
	}
	$ids = array_values( array_filter( array_map( 'absint', (array) $ids ) ) );
	if ( empty( $ids ) ) {
		return rotulos_fail( $redirect_to, __( 'Select at least one order to print.', 'rotulos-envio' ) );
	}

	$url    = (string) get_option( ROTULOS_OPTION_URL, '' );
	$store  = (string) get_option( ROTULOS_OPTION_STORE, '' );
	$secret = (string) get_option( ROTULOS_OPTION_SECRET, '' );
	if ( '' === $url || '' === $store || '' === $secret ) {
		return rotulos_fail(
			$redirect_to,
			__( 'This plugin is not linked to the shipping labels service yet. Connect the store from the service and press "Check plugin" there.', 'rotulos-envio' )
		);
	}

	// Shipping calls Andreani once per order: give it more time.
	$response = rotulos_signed_post( $url, array( 'ids' => $ids, 'action' => $service_action ), 'dispatch' === $service_action ? 90 : 30 );
	if ( is_wp_error( $response ) ) {
		return rotulos_fail( $redirect_to, __( 'Could not reach the shipping labels service. Please try again in a few minutes.', 'rotulos-envio' ) );
	}
	$data = json_decode( wp_remote_retrieve_body( $response ), true );
	$code = (int) wp_remote_retrieve_response_code( $response );
	if ( 200 !== $code || empty( $data['url'] ) ) {
		// The service's own message is already in the merchant's language.
		$detail = is_array( $data ) && ! empty( $data['detail'] )
			? (string) $data['detail']
			: __( 'The shipping labels service could not prepare the labels.', 'rotulos-envio' );
		return rotulos_fail( $redirect_to, $detail );
	}

	// Shipped some but not all: say which ones failed next time the order
	// list loads (the PDF opens in its own tab).
	if ( ! empty( $data['failed'] ) && is_array( $data['failed'] ) ) {
		$details = array();
		foreach ( $data['failed'] as $item ) {
			$details[] = '#' . ( isset( $item['number'] ) ? $item['number'] : '?' ) . ': ' . ( isset( $item['detail'] ) ? $item['detail'] : '' );
		}
		rotulos_fail( $redirect_to, __( 'Some orders could not be shipped:', 'rotulos-envio' ) . ' ' . implode( ' · ', $details ) );
	}

	wp_safe_redirect( esc_url_raw( $data['url'] ) );
	exit;
}
add_filter( 'handle_bulk_actions-woocommerce_page_wc-orders', 'rotulos_handle_bulk_action', 10, 3 );
add_filter( 'handle_bulk_actions-edit-shop_order', 'rotulos_handle_bulk_action', 10, 3 );

// The PDF lives on the service's host: allow exactly that host, nothing else.
add_filter(
	'allowed_redirect_hosts',
	function ( $hosts ) {
		$host = wp_parse_url( (string) get_option( ROTULOS_OPTION_URL, '' ), PHP_URL_HOST );
		if ( $host ) {
			$hosts[] = $host;
		}
		return $hosts;
	}
);

/**
 * POST to the service, signed the way it expects: the store's number and a
 * timestamp go in the body, and the body is signed with the shared secret.
 * Used by printing and by the checkout shipping quote.
 */
function rotulos_signed_post( $url, array $payload, $timeout ) {
	$body = wp_json_encode(
		array_merge(
			$payload,
			array(
				'store' => (string) get_option( ROTULOS_OPTION_STORE, '' ),
				'ts'    => time(),
			)
		)
	);
	return wp_remote_post(
		$url,
		array(
			'timeout' => $timeout,
			'headers' => array(
				'Content-Type'        => 'application/json',
				'X-Rotulos-Signature' => base64_encode( hash_hmac( 'sha256', $body, (string) get_option( ROTULOS_OPTION_SECRET, '' ), true ) ),
			),
			'body'    => $body,
		)
	);
}

function rotulos_fail( $redirect_to, $message ) {
	set_transient( 'rotulos_notice_' . get_current_user_id(), $message, 120 );
	return $redirect_to;
}

add_action(
	'admin_notices',
	function () {
		$key     = 'rotulos_notice_' . get_current_user_id();
		$message = get_transient( $key );
		if ( $message ) {
			delete_transient( $key );
			printf(
				'<div class="notice notice-error is-dismissible"><p><strong>%s</strong> %s</p></div>',
				esc_html__( 'Shipping labels:', 'rotulos-envio' ),
				esc_html( $message )
			);
		}
	}
);

// The PDF opens in a new tab, so the merchant keeps their order list.
add_action(
	'admin_enqueue_scripts',
	function () {
		$screen = function_exists( 'get_current_screen' ) ? get_current_screen() : null;
		if ( ! $screen || ! in_array( $screen->id, array( 'woocommerce_page_wc-orders', 'edit-shop_order' ), true ) ) {
			return;
		}
		wp_register_script( 'rotulos-envio', false, array(), '1.3.0', true );
		wp_enqueue_script( 'rotulos-envio' );
		wp_add_inline_script(
			'rotulos-envio',
			'document.addEventListener("submit", function (event) {
				var form = event.target;
				if (!form || !form.querySelector) return;
				var top = form.querySelector(\'select[name="action"]\');
				var bottom = form.querySelector(\'select[name="action2"]\');
				var chosen = (event.submitter && event.submitter.id === "doaction2" && bottom) ? bottom.value : (top ? top.value : "");
				if (' . wp_json_encode( array_keys( ROTULOS_ACTIONS ) ) . '.indexOf(chosen) !== -1) {
					form.target = "_blank";
					setTimeout(function () { form.removeAttribute("target"); }, 0);
				}
			}, true);'
		);
	}
);
