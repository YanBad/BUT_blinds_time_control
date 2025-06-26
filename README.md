I used google's Gemini AI to implement the fixes, so if there are any issues, feel free to comment. 


-----

# Blinds Controller (Enhanced Version)

**This project is a fork of the original [BUT\_blinds\_time\_control](https://github.com/MatthewOnTour/BUT_blinds_time_control) by [MatthewOnTour](https://github.com/MatthewOnTour).**

This enhanced version includes several bug fixes and stability improvements to provide a more reliable and feature-rich experience. A huge thank you to the original author for creating this excellent Home Assistant integration.

-----

## Overview

This custom integration for Home Assistant allows you to control time-based blinds. It's designed for blinds that are controlled by simple "up" and "down" switches (like relays) but lack native position feedback. The integration calculates the position of your blinds by tracking the travel time.

### Key Features

  * **Standard Cover Controls**: Provides Open, Close, Stop, and Set Position controls.
  * **Tilt Support**: Offers optional support for tilting the blinds.
  * **State Restoration**: Remembers the last known position of your blinds after a Home Assistant restart.
  * **Manual Recalibration**: Includes a service to manually set the position if it ever gets out of sync.
  * **Configurable Delays**: Supports a startup delay to account for motor response time and an interlock delay to protect the motor.
  * **UI Configuration**: Fully configurable through the Home Assistant user interface.

## Installation

#### HACS (Recommended)

1.  Go to the HACS page in Home Assistant.
2.  Click on "Integrations", then click the three dots in the top right and select "Custom repositories".
3.  Add the URL to your GitHub repository and select the "Integration" category.
4.  The `Blinds Controller` integration will now be available to install from the HACS page.
5.  Restart Home Assistant after installation.

#### Manual Installation

1.  Copy all files from the `custom_components/blinds_controller` directory of this repository.
2.  Paste them into the `/custom_components/blinds_controller/` directory in your Home Assistant configuration folder.
3.  Restart Home Assistant.

## Configuration

1.  In Home Assistant, go to **Settings -\> Devices & Services**.
2.  Click **Add Integration** and search for **Blinds Control**.
3.  Follow the on-screen instructions:
      * Give your blinds a unique name.
      * Select the switch entities for moving the blinds up and down.
      * Set the travel times in seconds for a full up and down cycle.
      * (Optional) Set the tilt times if your blinds support it, or leave them as `0.0` to disable tilt features.
      * (Optional) Configure a startup delay if your motor takes time to respond.

**Important Note:** After setup, the integration will assume the blinds are fully closed (0% position). If they are in a different position, you can easily correct this by calling the `blinds_controller.set_known_position` service or by simply moving the blinds to the fully open or closed position.

You can edit your configuration at any time from the integration's card.

## Automation

This cover entity will work with all standard Home Assistant automations. You can use services like `cover.set_cover_position` to control it in your scripts and automations.


## Support and Contribution

If you run into any issues or have a feature request, please [open an issue on the GitHub issues page](https://www.google.com/search?q=https://github.com/YourGitHubUsername/BUT_blinds_time_control/issues).

## Acknowledgements

This work was originally based on and inspired by this insightful [community post](https://community.home-assistant.io/t/custom-component-cover-time-based/187654) and the work of [MatthewOnTour](https://github.com/MatthewOnTour).

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.
