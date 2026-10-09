<?php
/**
 * Shipping method "Rótulos de envío": quotes shipping at checkout from the
 * store's rates table in the shipping labels service.
 *
 * The merchant adds it to their shipping zones (WooCommerce → Settings →
 * Shipping → zone → Add shipping method). At checkout it sends the
 * destination postcode and the cart weight in kilograms, signed like the
 * print request, and offers one rate per option the service answers with.
 *
 * It sits in the middle of someone else's sale, so it never breaks it: any
 * failure (no link yet, timeout, bad answer) just offers nothing, and the
 * store's other shipping methods are still there. Answers are cached for a
 * few minutes because WooCommerce recalculates shipping on every checkout
 * refresh.
 */

defined( 'ABSPATH' ) || exit;

const ROTULOS_RATES_TIMEOUT   = 5;
const ROTULOS_RATES_CACHE_TTL = 300;

add_action(
	'woocommerce_shipping_init',
	function () {
		if ( class_exists( 'Rotulos_Shipping_Method' ) ) {
			return;
		}

		class Rotulos_Shipping_Method extends WC_Shipping_Method {

			public function __construct( $instance_id = 0 ) {
				$this->id                 = 'rotulos';
				$this->instance_id        = absint( $instance_id );
				$this->method_title       = __( 'Shipping labels (rates table)', 'rotulos-envio' );
				$this->method_description = __( 'Quotes shipping from the rates table you load in the shipping labels service: destination postal code and cart weight. Destinations outside your table get no quote.', 'rotulos-envio' );
				$this->supports           = array( 'shipping-zones', 'instance-settings' );
				$this->instance_form_fields = array(
					'title' => array(
						'title'       => __( 'Name in the zone list', 'rotulos-envio' ),
						'type'        => 'text',
						'description' => __( 'Only shown in your admin. Buyers see the option names from your rates table.', 'rotulos-envio' ),
						'default'     => __( 'Shipping labels (rates table)', 'rotulos-envio' ),
					),
				);
				$this->init_settings();
				$this->title = $this->get_option( 'title', $this->method_title );
				add_action( 'woocommerce_update_options_shipping_' . $this->id, array( $this, 'process_admin_options' ) );
			}

			public function calculate_shipping( $package = array() ) {
				foreach ( rotulos_quote_package( $package ) as $rate ) {
					$this->add_rate(
						array(
							'id'        => $this->get_rate_id( $rate['code'] ),
							'label'     => $rate['label'],
							'cost'      => $rate['price'],
							'package'   => $package,
							'meta_data' => array( 'rotulos_option' => $rate['code'] ),
						)
					);
				}
			}
		}
	}
);

add_filter(
	'woocommerce_shipping_methods',
	function ( $methods ) {
		$methods['rotulos'] = 'Rotulos_Shipping_Method';
		return $methods;
	}
);

/**
 * The rates for a WooCommerce shipping package: a list of
 * `array( code, label, price )`, empty when there is nothing to offer.
 */
function rotulos_quote_package( $package ) {
	$url = (string) get_option( ROTULOS_OPTION_RATES, '' );
	if ( '' === $url || '' === (string) get_option( ROTULOS_OPTION_SECRET, '' ) ) {
		return array();
	}
	$destination = isset( $package['destination'] ) ? (array) $package['destination'] : array();
	$postcode    = isset( $destination['postcode'] ) ? trim( (string) $destination['postcode'] ) : '';
	if ( '' === $postcode ) {
		// WooCommerce asks again once the buyer types the postcode.
		return array();
	}
	$country = isset( $destination['country'] ) ? (string) $destination['country'] : '';
	$weight  = rotulos_package_weight_kg( $package );
	// The cart subtotal, for "free shipping from $X" rules set in the service.
	$total = isset( $package['contents_cost'] ) ? round( (float) $package['contents_cost'], 2 ) : 0.0;

	$cache_key = 'rotulos_rates_' . md5( wp_json_encode( array( $url, $postcode, $country, $weight, $total ) ) );
	$cached    = get_transient( $cache_key );
	if ( is_array( $cached ) ) {
		return $cached;
	}

	$response = rotulos_signed_post(
		$url,
		array(
			'postcode'   => $postcode,
			'country'    => $country,
			'weight_kg'  => $weight,
			'cart_total' => $total,
			'currency'   => get_woocommerce_currency(),
		),
		ROTULOS_RATES_TIMEOUT
	);
	if ( is_wp_error( $response ) || 200 !== (int) wp_remote_retrieve_response_code( $response ) ) {
		rotulos_log_quote_failure( $response );
		return array(); // Not cached: the next refresh tries again.
	}
	$data  = json_decode( wp_remote_retrieve_body( $response ), true );
	$rates = array();
	foreach ( ( is_array( $data ) && isset( $data['rates'] ) && is_array( $data['rates'] ) ) ? $data['rates'] : array() as $rate ) {
		if ( ! is_array( $rate ) || empty( $rate['code'] ) || ! isset( $rate['price'] ) || ! is_numeric( $rate['price'] ) ) {
			continue;
		}
		// A price in another currency would be charged as if it were the store's.
		if ( ! empty( $rate['currency'] ) && strtoupper( (string) $rate['currency'] ) !== strtoupper( get_woocommerce_currency() ) ) {
			continue;
		}
		$rates[] = array(
			'code'  => sanitize_key( (string) $rate['code'] ),
			'label' => rotulos_rate_label( $rate ),
			'price' => (float) $rate['price'],
		);
	}
	set_transient( $cache_key, $rates, ROTULOS_RATES_CACHE_TTL );
	return $rates;
}

/** Cart weight in kilograms, whatever unit the store loads weights in. */
function rotulos_package_weight_kg( $package ) {
	$total = 0.0;
	foreach ( isset( $package['contents'] ) ? (array) $package['contents'] : array() as $item ) {
		$product = isset( $item['data'] ) ? $item['data'] : null;
		if ( ! $product || ! is_a( $product, 'WC_Product' ) || ! $product->needs_shipping() ) {
			continue;
		}
		$weight = (float) $product->get_weight();
		if ( $weight > 0 ) {
			$total += wc_get_weight( $weight, 'kg' ) * max( 1, (int) $item['quantity'] );
		}
	}
	return round( $total, 3 );
}

/** "Envío estándar (3 a 5 días hábiles)": the table's name plus its delivery time. */
function rotulos_rate_label( $rate ) {
	$name = sanitize_text_field( (string) ( isset( $rate['name'] ) && '' !== $rate['name'] ? $rate['name'] : $rate['code'] ) );
	$min  = isset( $rate['delivery_days_min'] ) && is_numeric( $rate['delivery_days_min'] ) ? (int) $rate['delivery_days_min'] : null;
	$max  = isset( $rate['delivery_days_max'] ) && is_numeric( $rate['delivery_days_max'] ) ? (int) $rate['delivery_days_max'] : null;
	if ( null !== $min && null !== $max && $min !== $max ) {
		/* translators: 1: shipping option name, 2: minimum days, 3: maximum days. */
		return sprintf( __( '%1$s (%2$d to %3$d business days)', 'rotulos-envio' ), $name, $min, $max );
	}
	$days = null !== $max ? $max : $min;
	if ( null !== $days ) {
		/* translators: 1: shipping option name, 2: number of days. */
		return sprintf( _n( '%1$s (%2$d business day)', '%1$s (%2$d business days)', $days, 'rotulos-envio' ), $name, $days );
	}
	return $name;
}

function rotulos_log_quote_failure( $response ) {
	if ( ! function_exists( 'wc_get_logger' ) ) {
		return;
	}
	$detail = is_wp_error( $response )
		? $response->get_error_message()
		: 'HTTP ' . wp_remote_retrieve_response_code( $response );
	wc_get_logger()->warning( 'Shipping quote failed: ' . $detail, array( 'source' => 'rotulos-envio' ) );
}
