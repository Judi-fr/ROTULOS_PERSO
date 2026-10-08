<?php
/**
 * Removes what the plugin stored: the settings the shipping labels service
 * wrote. Orders are never touched.
 */

defined( 'WP_UNINSTALL_PLUGIN' ) || exit;

delete_option( 'rotulos_print_link_url' );
delete_option( 'rotulos_store_id' );
delete_option( 'rotulos_secret' );
delete_option( 'rotulos_rates_url' );

global $wpdb;
// Cached quotes (transients) of the shipping method.
$wpdb->query( "DELETE FROM {$wpdb->options} WHERE option_name LIKE '\\_transient\\_rotulos\\_rates\\_%' OR option_name LIKE '\\_transient\\_timeout\\_rotulos\\_rates\\_%'" );
