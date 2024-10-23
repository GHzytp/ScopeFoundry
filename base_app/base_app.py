"""
Created on Jul 23, 2014

Modified by Ed Barnard
UI enhancements by Ed Barnard, Alan Buckley
"""

from __future__ import absolute_import, division, print_function

import asyncio
import enum
import logging
import sys
import traceback
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
from qtpy import QtCore, QtWidgets

from ScopeFoundry import ini_io
from ScopeFoundry.helper_funcs import get_logger_from_class
from ScopeFoundry.logged_quantity import LoggedQuantity, LQCollection

from .logging_handlers import HtmlHandler
from .logging_widget import LoggingWidget

try:
    import IPython

    if (
        IPython.version_info[0] < 4
    ):  # compatibility for IPython < 4.0 (pre Jupyter split)
        from IPython.qt.console.rich_ipython_widget import (
            RichIPythonWidget as RichJupyterWidget,
        )
        from IPython.qt.inprocess import QtInProcessKernelManager
    else:
        from qtconsole.inprocess import QtInProcessKernelManager
        from qtconsole.rich_jupyter_widget import RichJupyterWidget
    CONSOLE_TYPE = "qtconsole"
except Exception as err:
    logging.warning(
        "ScopeFoundry unable to import iPython console, using pyqtgraph.console instead. Error: {}".format(
            err
        )
    )
    import pyqtgraph.console

    CONSOLE_TYPE = "pyqtgraph.console"


# See https://riverbankcomputing.com/pipermail/pyqt/2016-March/037136.html
# makes sure that unhandled exceptions in slots don't crash the whole app with PyQt 5.5 and higher
# old version:
## sys.excepthook = traceback.print_exception
# new version to send to logger
def log_unhandled_exception(*exc_info):
    text = "".join(traceback.format_exception(*exc_info))
    logging.critical("Unhandled exception:" + text)
    # print("Unhandled exception:" + text)


sys.excepthook = log_unhandled_exception


# To fix a bug with jupyter qtconsole for python 3.8
# https://github.com/jupyter/notebook/issues/4613#issuecomment-548992047

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Dark mode
try:
    import qdarktheme  # pip install pyqtdarktheme

    darktheme_available = True
except Exception as err:
    darktheme_available = False
    print(f"pyqdarktheme unavailable: {err}")


class WRITE_RES(enum.Enum):
    SUCCESS = enum.auto()
    MISSING = enum.auto()
    PROTECTED = enum.auto()


class BaseApp(QtCore.QObject):

    name = "ScopeFoundry"

    def __init__(self, argv=[], dark_mode=False):

        super().__init__()

        self.q_object = BaseAppQbject()

        self.log = get_logger_from_class(self)

        path = Path(__file__)
        self.this_path = path.parent
        self.this_filename = path.name

        self.qtapp = QtWidgets.QApplication.instance()
        if not self.qtapp:
            self.qtapp = QtWidgets.QApplication(argv)

        if dark_mode and darktheme_available:
            qdarktheme.setup_theme()

        self._setting_paths = {}
        self.settings = LQCollection(path="app")

        self.add_lq_collection_to_settings_path(self.settings)

        self.operations = OrderedDict()

        # auto creation of console widget
        try:
            self.setup_console_widget()
        except Exception as err:
            print("failed to setup console widget " + str(err))
            self.console_widget = QtWidgets.QWidget()

        self.setup_logging()

        self.qtapp.setApplicationName(self.name)

    def exec_(self):
        return self.qtapp.exec_()

    def setup_console_widget(self, kernel=None):
        """
        Create and return console QWidget. If Jupyter / IPython is installed
        this widget will be a full-featured IPython console. If Jupyter is unavailable
        it will fallback to a pyqtgraph.console.ConsoleWidget.

        If the app is started in an Jupyter notebook, the console will be
        connected to the notebook's IPython kernel.

        the returned console_widget will also be accessible as self.console_widget

        In order to see the console widget, remember to insert it into an existing
        window or call self.console_widget.show() to create a new window
        """
        if CONSOLE_TYPE == "pyqtgraph.console":
            self.console_widget = pyqtgraph.console.ConsoleWidget(
                namespace={"app": self, "pg": pg, "np": np}, text="ScopeFoundry Console"
            )
        elif CONSOLE_TYPE == "qtconsole":

            if kernel == None:
                try:  # try to find an existing kernel
                    # https://github.com/jupyter/notebook/blob/master/docs/source/examples/Notebook/Connecting%20with%20the%20Qt%20Console.ipynb
                    import ipykernel as kernel

                    conn_file = kernel.get_connection_file()
                    import qtconsole.qtconsoleapp

                    self.qtconsole_app = qtconsole.qtconsoleapp.JupyterQtConsoleApp()
                    self.console_widget = self.qtconsole_app.new_frontend_connection(
                        conn_file
                    )
                    self.console_widget.setWindowTitle("ScopeFoundry IPython Console")
                except:  # make your own new in-process kernel
                    # https://github.com/ipython/ipython-in-depth/blob/master/examples/Embedding/inprocess_qtconsole.py
                    self.kernel_manager = QtInProcessKernelManager()
                    self.kernel_manager.start_kernel()
                    self.kernel = self.kernel_manager.kernel
                    self.kernel.shell.banner1 += """
                    ScopeFoundry Console
                    
                    Variables:
                     * np: numpy package
                     * app: the ScopeFoundry App object
                    """
                    self.kernel.gui = "qt4"
                    self.kernel.shell.push({"np": np, "app": self})
                    self.kernel_client = self.kernel_manager.client()
                    self.kernel_client.start_channels()

                    # self.console_widget = RichIPythonWidget()
                    self.console_widget = RichJupyterWidget()
                    self.console_widget.setWindowTitle("ScopeFoundry IPython Console")
                    self.console_widget.kernel_manager = self.kernel_manager
                    self.console_widget.kernel_client = self.kernel_client
            else:
                import qtconsole.qtconsoleapp

                self.qtconsole_app = qtconsole.qtconsoleapp.JupyterQtConsoleApp()
                self.console_widget = self.qtconsole_app.new_frontend_connection(
                    kernel.get_connection_file()
                )
                self.console_widget.setWindowTitle("ScopeFoundry IPython Console")
        else:
            raise ValueError("CONSOLE_TYPE undefined")

        return self.console_widget

    def setup(self):
        pass

    def settings_save_ini_ask(self, dir=None, save_ro=True):
        """Opens a Save dialogue asking the user to select a save destination and give the save file a filename. Saves settings to an .ini file."""
        # TODO add default directory, etc
        fname, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.ui, caption="Save Settings", dir="", filter="Settings (*.ini)"
        )
        # print(repr(fname))
        if fname:
            self.settings_save_ini(fname, save_ro=save_ro)
        return fname

    def settings_load_ini_ask(self, dir=None):
        """Opens a Load dialogue asking the user which .ini file to load into our app settings. Loads settings from an .ini file."""
        # TODO add default directory, etc
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Settings (*.ini)")
        if fname:
            self.settings_load_ini(fname)
        return fname

    def setup_logging(self):

        logging.basicConfig(level=logging.WARN)
        logging.getLogger("traitlets").setLevel(logging.WARN)
        logging.getLogger("ipykernel.inprocess").setLevel(logging.WARN)
        logging.getLogger("LoggedQuantity").setLevel(logging.WARN)
        logging.getLogger("PyQt5").setLevel(logging.WARN)

        self.logging_widget = LoggingWidget()
        handler = HtmlHandler(level=logging.DEBUG)
        handler.new_log_signal.connect(self.logging_widget.on_new_log)
        logging.getLogger().addHandler(handler)

    def add_sub_tree(self, tree: QtWidgets.QTreeWidget, sub_tree): ...

    def on_right_click(self): ...

    def add_setting_path(self, lq: LoggedQuantity):
        self._setting_paths[lq.path] = lq

    def remove_setting_path(self, lq: LoggedQuantity):
        self._setting_paths.pop(lq.path, None)

    def add_lq_collection_to_settings_path(self, settings: LQCollection):
        settings.q_object.new_lq_added.connect(self.add_setting_path)
        settings.q_object.lq_removed.connect(self.remove_setting_path)
        for lq in settings.as_dict().values():
            self.add_setting_path(lq)

    def write_setting(self, path: str, value):
        lq = self.get_lq(path)
        if lq is None:
            return WRITE_RES.MISSING
        lq.update_value(value)
        return WRITE_RES.SUCCESS

    def write_setting_safe(self, path: str, value):
        lq = self.get_lq(path)
        if lq is None:
            return WRITE_RES.MISSING
        elif lq.protected:
            return WRITE_RES.PROTECTED
        lq.update_value(value)
        return WRITE_RES.SUCCESS

    def get_lq(self, path: str) -> LoggedQuantity:
        """
        returns the LoggedQuantity defined by a path string.
        """
        return self._setting_paths.get(path, None)

    def write_settings_safe(self, settings):
        """
        updates settings based on a dictionary, silently ignores protected logged quantities and non-existing.

        ==============  =========  ====================================================================================
        **Arguments:**  **Type:**  **Description:**
        settings        dict       (path, value) map
        ==============  =========  ====================================================================================
        """
        report = {}
        for path, value in settings.items():
            success = self.write_setting_safe(path, value)
            report[path] = success
        return report

    def settings_save_ini(self, fname):
        """
        ==============  =========  ==============================================
        **Arguments:**  **Type:**  **Description:**
        fname           str        relative path to the filename of the ini file.
        ==============  =========  ==============================================
        """
        settings = self.read_settings(None, True)
        ini_io.save_settings(fname, settings)

        self.propose_settings_values(Path(fname).name, settings)

        self.log.info(f"ini settings saved to {fname} str")

    def settings_load_ini(self, fname):
        """
        ==============  =========  ==============================================
        **Arguments:**  **Type:**  **Description:**
        fname           str        relative path to the filename of the ini file.
        ==============  =========  ==============================================
        """
        settings = ini_io.load_settings(fname)
        self.write_settings_safe(settings)
        self.propose_settings_values(Path(fname).name, settings)
        return settings

    def propose_settings_values(self, name: str, settings):
        """
        Adds to proposed_values of LQs.
        proposed_values can be inspected with right click on connected widgets

        ==============  =========  ====================================================================================
        **Arguments:**  **Type:**  **Description:**
        name            str        label of the proposed value
        settings        dict       (path, value) map
        ==============  =========  ====================================================================================
        """
        for path, val in settings.items():
            lq = self.get_lq(path)
            if lq is None:
                continue
            lq.propose_value(name, val)

    def read_settings(self, paths=None, ini_string_value=False):
        """
        returns a dictionary (path, value):
        ================== =========  =============================================================================
        **Arguments:**     **Type:**  **Description:**
        paths              list[str]  paths to setting, if None(default) all paths are used
        ================== =========  =============================================================================
        """
        paths = self._setting_paths if paths is None else paths
        return {p: self.read_setting(p, ini_string_value) for p in paths}

    def read_setting(self, path: str, ini_string_value=False):
        lq = self.get_lq(path)
        if ini_string_value:
            return lq.ini_string_value()
        return lq.val

    def add_operation(self, name: str, op_func: Callable[[], None]):
        """
        Create an operation for the App.

        *op_func* is a function that will be called upon operation activation

        operations are typically exposed in the default ScopeFoundry gui via a pushButton

        :type name: str
        :type op_func: QtCore.Slot or Callable without Argument
        """
        self.operations[name] = op_func
        self.q_object.operation_added.emit(name)

    def remove_operation(self, name):
        if name not in self.operations:
            return
        del self.operations[name]
        self.q_object.operation_removed.emit(name)


class BaseAppQbject(QtCore.QObject):

    operation_added = QtCore.Signal(str)
    operation_removed = QtCore.Signal(str)


class TestBaseApp(BaseApp):

    name = "test base app"

    def __init__(self, argv=[], dark_mode=False):
        super().__init__(argv, dark_mode)

        self.ui = QtWidgets.QWidget()
        # self.ui = new_tree((self,), ["test", ""])
        self.ui.show()
        self.console_widget.show()
        # self.logging_widget.show()


if __name__ == "__main__":
    # app = BaseApp(sys.argv)
    app = TestBaseApp(sys.argv)
    sys.exit(app.exec_())
