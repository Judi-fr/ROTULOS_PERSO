=== Rótulos de envío ===
Tags: woocommerce, shipping, shipping labels, labels, print
Requires at least: 6.5
Tested up to: 7.1
Requires PHP: 7.4
Stable tag: 1.1.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Print shipping labels for the selected orders straight from the WooCommerce order list, and quote shipping at checkout from your rates table.

== Description ==

Adds a "Print shipping labels" bulk action to WooCommerce → Orders. Tick the orders, choose the action, and a PDF with one label per order opens in a new tab, in the order you picked them.

Each label carries what the carrier needs to deliver the parcel: sender, recipient, address, postal code, city and province, order number, QR and barcode. Products, prices, payment details and the buyer's email or phone are never printed.

The labels use the design, sender and logo the store has in the shipping labels service, so they look the same whether they are printed from WooCommerce or from the service.

This plugin is the WooCommerce side of the shipping labels service: the store must be connected to it first. There is nothing to configure in WordPress — when the store is connected, the service fills in this plugin's settings by itself.

Works with both WooCommerce order screens (High-Performance Order Storage and the legacy posts screen).

It also adds a shipping method, "Shipping labels (rates table)", that quotes shipping at checkout from the rates table the store loads in the service: destination postal code and cart weight in, one price per shipping option out. Add it to your shipping zones in WooCommerce → Settings → Shipping. Destinations outside the table get no quote, and if the service cannot be reached the method simply offers nothing — your other shipping methods keep working.

== External services ==

This plugin connects to the shipping labels service the store was connected to, to generate the labels. It is needed: the labels are drawn by that service, with the store's own design.

* What is sent, and when: only when someone with permission to manage orders runs the "Print shipping labels" bulk action, the plugin sends the IDs of the selected orders, the store's number in the service and a timestamp, signed with the store's secret. No buyer data is sent by the plugin; the service already holds the orders it needs through the WooCommerce REST API keys the store owner granted when connecting.
* Where: the address the service wrote in this plugin's settings when the store was connected (WooCommerce → REST API settings group "Shipping labels").
* The browser is then sent to a short-lived link on that same service, which returns the PDF.
* At checkout, only when this plugin's shipping method is added to the buyer's shipping zone: the plugin sends the destination postal code and country, the total cart weight and the store currency, signed the same way, to get the shipping prices. No name, address line, email or phone of the buyer is sent. Answers are cached for 5 minutes.

== Installation ==

1. Connect your WooCommerce store to the shipping labels service.
2. Install and activate this plugin (Plugins → Add New).
3. In the service, open your store and press "Check plugin". From then on, "Print shipping labels" appears under Bulk actions in WooCommerce → Orders.

== Frequently Asked Questions ==

= The bulk action says the plugin is not linked =

The service has not written this plugin's settings yet. Open your store in the service and press "Check plugin". It is also done automatically every 30 minutes.

= Why doesn't the shipping method show at checkout? =

Check that it is added to the buyer's shipping zone, that the service has a rate for that postal code and cart weight, and that the rate's currency is the store's currency.

= Does it change my orders? =

No. Printing only reads the selected orders. Marking an order as shipped is done from the service.

== Changelog ==

= 1.1.0 =
* Shipping method that quotes shipping at checkout from the store's rates table.

= 1.0.0 =
* First version: "Print shipping labels" bulk action on both order screens.
