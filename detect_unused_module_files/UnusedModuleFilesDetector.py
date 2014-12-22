#coding:utf8

import fnmatch
import os


class UnusedModuleFilesDetected:

    """ Class to determine if file in a module are not being used """

    def __init__(self, path):
        """ ATTRIBUTES:
            - self.module_absolute_path:
            String to save the absolute path to the module
            - self.module_absolute_path_files:
            Dictionary with keys filenames and values
            absolute path to each file
            - self.used_files_in_module:
            List the files imported (used) in the __init__.py
            and __openerp__.py
            - self.used_files_source:
            Absolute path files for __init__.py and __openerp__.py files
            - self.classified_files:
            Filenames sorted by extension
            - self.module_files:
            filenames for this module 
            - self.sources_relative_directories:
            directory names for files that functions like sources
            - self.result:
            string that contains final analysis report """
        self.module_absolute_path = path
        self.module_absolute_path_files = {}
        self.used_files_in_module = []
        self.used_files_source = []
        self.classified_files = {}
        self.module_files = []
        self.sources_relative_directories = []
        self.result = ""
        self.module_name = ""

    def get_all_module_files(self):
        """ Create structures with all files path and name information """
        
        # Getting the module name
        self.module_name = self.module_absolute_path[self.module_absolute_path.rfind('/') + 1:]
        # Getting all files from a directory
        for root, dirnames, filenames in os.walk(self.module_absolute_path):
            for filename in fnmatch.filter(filenames, '*.*'):
                if '.pyc' not in filename:
                    absolute_file_name = os.path.join(root, filename)
                    if filename in ("__init__.py", "__openerp__.py"):
                        if filename == '__init__.py':
                            directory = root[root.rfind('/')+1:]
                            if directory != self.module_name:
                                self.sources_relative_directories.append(directory)
                        self.used_files_source.append(absolute_file_name)
                    self.module_absolute_path_files.update({filename: absolute_file_name})
                    self.module_files.append(filename)

        # Classifying files by extension
        for each_file in self.module_files:
            # Getting the extension
            temp_string = each_file[: : -1]
            extension = temp_string[: temp_string.find(".") + 1][: : -1]
            # If dictionary does not have the obtained extension, add it
            if extension not in self.classified_files.keys():
                self.classified_files.update({extension: []})
            # Add the file to his respecting group in the dictionary
            self.classified_files.get(extension).append(each_file)

        self.result += "1. GETTING ALL MODULE FILES\n\n"\
            "<c> Your module has the next files: %s\n" % len(self.module_files)
        for module_file in self.module_files:
            self.result += "\t¬ %s\n" % module_file
        self.result += "\n<c> The files extensions managed are:\n\n"
        for extension in self.classified_files.keys():
            self.result += "\t¬ %s\n" % extension
        self.result += "\n<c> Classified by type your files are so:\n\n"
        for extension in self.classified_files.keys():
            self.result += "\t¬ Files for %s extension: %s\n" % (extension, len(self.classified_files.get(extension)))
            for file_in_type in self.classified_files.get(extension):
                self.result += "\t\t- %s\n" % file_in_type
        self.result += "\n<c> Source files (absolute paths for any file that are "\
            "going to tell us what files were used in the module)\n\n"
        for source_file in self.used_files_source:
            self.result += "¬ %s\n" % source_file

    def get_used_files(self):
        """ Create an structure with used files """
        
        for used_source in self.used_files_source:
            if "__init__.py" in used_source:
                temp_file = open(used_source, 'r')
                for file_line in temp_file.readlines():
                    if "fast_suite" not in file_line:
                        if "import" in file_line[: file_line.find('#')] and file_line != "\n":
                            is_not_directory_import = True
                            for source_dir in self.sources_relative_directories:
                                if "import %s" % source_dir in file_line:
                                    is_directory_import = False
                                    break
                            if "import model" not in file_line and is_not_directory_import:
                                self.used_files_in_module.append(file_line[file_line.find("import")+7:-1]+".py")
                    else:
                        temp_file1 = open(used_source, 'r')
                        file_in_string = temp_file1.read()
                        list_fs_files = file_in_string[file_in_string.find('['):file_in_string.find(']')+1]
                        list_fs_files = self.aux_clean_openerp_py_string(list_fs_files).split('\n')
                        list_fs_files = [fs_file + '.py' for fs_file in list_fs_files if fs_file is not '']
                        self.used_files_in_module += list_fs_files
            if "__openerp__.py" in used_source:
                temp_file = open(used_source, 'r')
                start_index = 0
                start_list_index = 0
                final_list_index = 0
                string_to_list = ""
                available_file_lists = (
                    "'test'","'demo'","'data'","'qweb'","'images'",
                    "'init_xml'","'update_xml'","'demo_xml'","'security'",
                    "'css'","'js'",
                    '"test"','"demo"','"data"','"qweb"','"images"',
                    '"init_xml"','"update_xml"','"demo_xml"','"security"',
                    '"css"','"js"')
                file_in_string = temp_file.read()
                for file_list_name in available_file_lists:
                    start_index = file_in_string.find(file_list_name+":")
                    if start_index > -1:
                        start_list_index = file_in_string.find("[", start_index)
                        final_list_index = file_in_string.find("]", start_list_index)
                        list_of_files = self.aux_clean_openerp_py_string(file_in_string[start_list_index: final_list_index+1])
                        list_of_files = [u_file[u_file.rfind('/')+1:] for u_file in list_of_files.split('\n') if u_file not in '' and '#' not in u_file[: u_file.rfind(',')]]
                        list_of_files = [list_file for list_file in list_of_files if list_file is not []]
                        self.used_files_in_module += list_of_files

        self.result += "\n\n\n2. GETTING ALL USED FILE IN THE MODULE\n\n"\
            "<c> All used files via __init__.py & __openerp__.py are: %s\n\n" % len(self.used_files_in_module)
        for used_file in self.used_files_in_module:
            self.result += "\t¬ %s\n" % used_file
    
    def get_unused_files(self):
        """ Method to make the intersection between all files that are
            present in the module and all files that use explicitly in
            the code via __init__.py, __openerp__.py and  other source
            files """
        # Getting all module files and used files in the module, before do
        # a difference between the 2 sets
        self.get_all_module_files()
        self.get_used_files()
        U_module_files = None
        # DELETE PROCESS
        # Deleting the source files
        source_files = ("__init__.py", "__openerp__.py")
        U_module_files = [m_file for m_file in self.module_files if m_file not in source_files]
        # Deleting the files with extension that are used by default
        def_used_file_extensions = (".po", ".pot", ".png", ".jpg", ".gif")
        U_module_files = [m_file for m_file in U_module_files if m_file[m_file.rfind("."):] not in def_used_file_extensions]
        # SET'S DIFFERENCE
        unused_files = set(U_module_files) - set(self.used_files_in_module)
        self.result += "\n\n\n\n3. FINAL RESULT FOR %s\n\n" % self.module_name
        if len(unused_files) > 0:
            self.result += "<c> This files are not being used by your module in a explicitly way:\n\n"
            for unused in unused_files:
                abs_unused_path = self.module_absolute_path_files.get(unused)
                relative_path = abs_unused_path[abs_unused_path.find(self.module_absolute_path)+len(self.module_absolute_path)+1:]
                self.result += "\t¬ File: %s\n\tRelative path: %s\n\n" % (unused, relative_path)
        else:
            self.result += "<c> GREAT !!\n\n\t¬ ALL FILES ARE BEING USED IN THIS MODULE"
        return self.result

    def aux_clean_openerp_py_string(self, string):
        return string.replace(' ','').replace('[','').replace(']','').\
        replace('\"','').replace("\'",'').replace(',','').replace('\t','')


obj = UnusedModuleFilesDetected('/home/sergio/Documentos/VAUXOO/odoo/my_dev_branches/omlv2-pylint-flake8/l10n_mx_statement_base')
print obj.get_unused_files()



