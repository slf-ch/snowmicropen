import ConfigParser as Cfg
import csv
import logging
from datetime import datetime
from os.path import exists, splitext, split

import numpy as np

from pnt import Pnt
from snowmicropyn.analysis import detect_surface, detect_ground

log = logging.getLogger(__name__)


class Profile(object):
    def __init__(self, pnt_filename, ini_filename=None, name=None):
        # Set name of profile (by default pnt filename without extension)
        self.name = name
        if not name:
            # Use default name (Filename without extension
            self.name = splitext(split(pnt_filename)[1])[0]

        # Load pnt file, returns samples and header (dict)
        self.pnt_filename = pnt_filename
        self.samples, self.pnt_header = Pnt.load_pnt(pnt_filename)

        # Get a clean comment form pnt header (unfortunately not zero terminated string)
        length = self.pnt_header_value(Pnt.COMMENT_LENGTH)
        content = self.pnt_header_value(Pnt.COMMENT_CONTENT)
        self._comment = content[0:length]

        # Get clean WGS84 coordinates (use +/- instead of N/E)
        self.latitude = self.pnt_header_value(Pnt.GPS_WGS84_LATITUDE)
        self.longitude = self.pnt_header_value(Pnt.GPS_WGS84_LONGITUDE)
        north = self.pnt_header_value(Pnt.GPS_WGS84_NORTH)
        east = self.pnt_header_value(Pnt.GPS_WGS84_EAST)
        if north.upper() != 'N':
            self._latitude = -self._latitude
        if east.upper() != 'E':
            self._longitude = -self._longitude

        # Get a proper timestamp by putting pnt entries together
        self.timestamp = None
        year = self.pnt_header_value(Pnt.TIMESTAMP_YEAR)
        month = self.pnt_header_value(Pnt.TIMESTAMP_MONTH)
        day = self.pnt_header_value(Pnt.TIMESTAMP_DAY)
        hour = self.pnt_header_value(Pnt.TIMESTAMP_HOUR)
        minute = self.pnt_header_value(Pnt.TIMESTAMP_MINUTE)
        second = self.pnt_header_value(Pnt.TIMESTAMP_SECOND)
        try:
            self.timestamp = datetime(year, month, day, hour, minute, second)
        except ValueError:
            log.warn('Unable to build timestamp from pnt header info')

        # Get other important entries from header
        self.gps_pdop = self.pnt_header_value(Pnt.GPS_PDOP)
        self.gps_numsats = self.pnt_header_value(Pnt.GPS_NUMSATS)
        self.smp_serial = self.pnt_header_value(Pnt.SMP_SERIAL)
        self.smp_length = self.pnt_header_value(Pnt.SMP_MAXLENGTH)
        self.overload = self.pnt_header_value(Pnt.SENSOR_OVERLOAD)

        # When no ini file provided, use default name which
        # is same as pnt file but ini extension
        self.ini_filename = ini_filename
        if not self.ini_filename:
            self.ini_filename = splitext(self.pnt_filename)[0] + '.ini'

        self._ini = Cfg.ConfigParser()

        # Load ini file, if available
        if exists(self.ini_filename):
            log.info('Reading ini file {} for {}'.format(self.ini_filename, self))
            self._ini.read(self.ini_filename)

        # Ensure a section called 'markers' does exist
        if not self._ini.has_section('markers'):
            self._ini.add_section('markers')

    def __str__(self):
        length = self.distance_arr[-1] - self.distance_arr[0]
        return 'Profile(name={}, {:.3f} mm, {} samples)'.format(repr(self.name), length, len(self))

    def __len__(self):
        return len(self.distance_arr)

    @property
    def distance_arr(self):
        return self.samples[:, 0]

    @property
    def force_arr(self):
        return self.samples[:, 1]

    def pnt_header_value(self, pnt_id):
        return self.pnt_header[pnt_id].value

    @property
    def coordinates(self):
        return self.latitude, self.longitude

    @property
    def markers(self):
        return self._ini.items('markers')

    def marker(self, name, fallback=None):
        try:
            return self._ini.getfloat('markers', name)
        except Cfg.NoOptionError as e:
            if fallback:
                return fallback
            raise KeyError(e)

    def set_marker(self, name, value):
        return self._ini.set('markers', name, str(value))

    def remove_marker(self, name):
        self._ini.remove_option('markers', name)

    @staticmethod
    def load(pnt_filename, ini_filename=None):
        return Profile(pnt_filename, ini_filename)

    def save(self, ini_filename=None):
        filename = self.ini_filename
        if ini_filename:
            filename = ini_filename
        # Only write file if markers are present
        markers = self._ini.items('markers')
        if not markers:
            raise ValueError('Nothing to save, set some markers first')

        with open(filename, 'w') as f:
            log.info('Saving ini info of {} to file {}'.format(self, filename))
            self._ini.write(f)

    def export_samples(self, filename=None, precision=3, snowpack_only=False, relativize_distance=False):
        if not filename:
            filename = splitext(self.pnt_filename)[0] + '_samples.csv'
        log.info('Exporting samples of {} to {}'.format(self, filename))
        samples = self.samples
        if snowpack_only:
            samples = self.snowpack(relativize_distance)
        header = 'Depth [mm],Force [N]'
        fmt = '%.{}f'.format(precision)
        np.savetxt(filename, samples, delimiter=',', header=header, comments='', fmt=fmt)

    def export_meta(self, filename=None, full_pnt_header=False):
        if not filename:
            filename = splitext(self.pnt_filename)[0] + '_meta.csv'
        log.info('Exporting meta information of {} to {}'.format(self, filename))
        with open(filename, 'w') as f:
            writer = csv.writer(f)
            writer.writerow(['key', 'value'])  # CSV header
            # Export important properties of profile
            writer.writerow(('name', self.name))
            writer.writerow(('pnt_file', self.pnt_filename))
            writer.writerow(('gps.coords.lat', self.latitude))
            writer.writerow(('gps.coords.long', self.longitude))
            writer.writerow(('gps.numsats', self.gps_numsats))
            writer.writerow(('gps.pdop', self.gps_pdop))
            writer.writerow(('smp.serial', self.smp_serial))
            writer.writerow(('smp.length', self.smp_length))
            # Export markers
            for k, v in self.markers:
                writer.writerow(('ini.marker.' + k, v))
            # Export pnt header entries
            if full_pnt_header:
                for pnt_id, (label, value, unit) in sorted(self.pnt_header.items()):
                    writer.writerow(['pnt.' + pnt_id, value])

    @property
    def max_force_sample(self):
        """ Get max force in this profile
        :return: Tuple with max force value and its distance
        """
        index = np.argmax(self.samples[:, 1])
        return self.samples[index]

    def samples_within(self, start, end, relativize_distance=False):
        """Return samples which within a range.

        :param start: Start of distance of interest. When ``None``,
            start of Profile is used.
        :param end: End of distance of interest. When ``None``, end of
            the Profile is used.
        :param relativize_distance: Default set to ``False``. When set
            to ``True``, the distance in the samples returned starts
            with 0.
        :return: Samples within the range.
        """

        # In case limits are None, use start begin or end of profile
        if start is None:
            start = self.distance_arr[0]
        if end is None:
            end = self.distance_arr[-1]

        # Flip range if necessary, so lower number is always first
        if start >= end:
            end, start = start, end

        condition = np.logical_and(self.distance_arr > start, self.distance_arr < end)
        samples = self.samples[condition]

        # Subtract offset to get relative distance
        if relativize_distance:
            offset = samples[0, 0]
            samples = np.subtract(samples, [offset, 0])
        return samples

    def snowpack(self, relativize_distance=True):
        try:
            s = self.marker('surface')
            g = self.marker('ground')
            return self.samples_within(s, g, relativize_distance)
        except KeyError as e:
            raise KeyError('Required marker missing in {}. Error: {}'.format(self, e))

    def detect_surface(self, exception_on_failure=False):
        # TODO: Implement raise_on_failure in detect_surface
        surface = detect_surface(self.samples)
        self.set_marker('surface', surface)
        return surface

    def detect_ground(self, exception_on_failure=False):
        # TODO: Implement raise_on_failure in detect_ground
        ground = detect_ground(self.samples, self.overload)
        self.set_marker('ground', ground)
        return ground